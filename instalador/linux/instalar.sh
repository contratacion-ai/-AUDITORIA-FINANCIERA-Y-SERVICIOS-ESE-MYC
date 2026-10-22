#!/usr/bin/env bash
# Instalador de Costos ABC para Linux (Ubuntu 22.04+/Debian 12+/RHEL 9+).
#   sudo ./instalar.sh            → instalación nativa: Python + systemd (SQLite o PostgreSQL existente)
#   sudo ./instalar.sh --docker   → Docker Compose con PostgreSQL
# Variables opcionales: DESTINO (/opt/costos-abc), PUERTO (8000), ABC_ADMIN_PASSWORD, SIN_PREGUNTAS=1
set -euo pipefail

DESTINO="${DESTINO:-/opt/costos-abc}"
PUERTO="${PUERTO:-8000}"
USUARIO_SO="costosabc"
ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODO="nativo"; [[ "${1:-}" == "--docker" ]] && MODO="docker"

paso() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
ok() { printf '    \033[32m%s\033[0m\n' "$1"; }
preguntar() {  # preguntar "texto" "defecto" → respuesta en $R
  if [[ "${SIN_PREGUNTAS:-0}" == "1" ]]; then R="$2"; return; fi
  read -r -p "$1 [$2]: " R; R="${R:-$2}"
}
aleatorio() { python3 -c "import secrets;print(secrets.token_urlsafe(50))"; }

[[ $EUID -eq 0 ]] || { echo "Ejecute con sudo."; exit 1; }
echo "=== Instalador · Herramienta de costos ABC para instituciones de salud ($MODO) ==="

paso "Copiando la aplicación a $DESTINO"
mkdir -p "$DESTINO"
if [[ "$ORIGEN" != "$DESTINO" ]]; then
  tar -C "$ORIGEN" --exclude=.venv --exclude=.git --exclude=datos --exclude=media --exclude=staticfiles \
      --exclude=.env --exclude=db.sqlite3 --exclude='__pycache__' -cf - . | tar -C "$DESTINO" -xf -
fi
mkdir -p "$DESTINO/datos/media" "$DESTINO/datos/respaldos"
ok "Archivos copiados"

paso "Configuración"
if [[ -f "$DESTINO/.env" ]]; then
  ok ".env existente conservado"
else
  preguntar "Nombres/IP de acceso (coma)" "localhost,127.0.0.1,$(hostname),$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//')"; HOSTS="$R"
  preguntar "¿Habrá certificado HTTPS (proxy nginx/Apache)? (s/n)" "n"; [[ "$R" =~ ^[sS] ]] && HTTPS=1 || HTTPS=0
  ORIGENES=$(echo "$HOSTS" | tr ',' '\n' | sed '/^$/d' | while read -r h; do
    if [[ $HTTPS == 1 ]]; then echo -n "https://$h,"; else echo -n "http://$h:$PUERTO,"; fi; done | sed 's/,$//')
  if [[ $MODO == docker ]]; then
    PG=$(aleatorio | tr -dc 'A-Za-z0-9' | head -c 32)
    DBLINEA="POSTGRES_PASSWORD=$PG"
  else
    preguntar "URL de PostgreSQL (vacío = SQLite local)" ""
    DBLINEA="DATABASE_URL=${R:-sqlite:///$DESTINO/datos/db.sqlite3}"
  fi
  cat > "$DESTINO/.env" <<ENV
DJANGO_ENV=production
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$(aleatorio)
# No cambie PSEUDONIMO_KEY después de cargar RIPS o facturación
PSEUDONIMO_KEY=$(aleatorio)
DJANGO_ALLOWED_HOSTS=$HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS=$ORIGENES
USAR_HTTPS=$HTTPS
SECURE_SSL_REDIRECT=$HTTPS
MEDIA_ROOT=$([[ $MODO == docker ]] && echo /app/media || echo "$DESTINO/datos/media")
ABC_PUERTO=$PUERTO
CARGA_MAX_MB=25
SESSION_COOKIE_AGE=1800
$DBLINEA
ENV
  chmod 600 "$DESTINO/.env"
  ok ".env creado con claves aleatorias (permisos 600)"
fi

paso "Institución y administrador"
preguntar "Código de la institución (REPS o interno)" "INST-001"; CODIGO="$R"
preguntar "Nombre de la institución" "ESE HOSPITAL"; NOMBRE="$R"
preguntar "NIT" ""; NIT="$R"
preguntar "Municipio" ""; MUNICIPIO="$R"
preguntar "Usuario administrador" "admin"; ADMIN="$R"
if [[ -z "${ABC_ADMIN_PASSWORD:-}" ]]; then
  while true; do
    read -r -s -p "Contraseña del administrador (mínimo 10 caracteres): " C1; echo
    read -r -s -p "Repita la contraseña: " C2; echo
    [[ "$C1" == "$C2" ]] && break; echo "No coinciden."
  done
  export ABC_ADMIN_PASSWORD="$C1"
fi
preguntar "¿Crear también la institución de demostración (datos sintéticos)? (s/n)" "n"; [[ "$R" =~ ^[sS] ]] && DEMO="--demo" || DEMO=""
ARGS=(instalar --institucion-codigo "$CODIGO" --institucion-nombre "$NOMBRE" --nit "$NIT" --municipio "$MUNICIPIO" --usuario "$ADMIN" $DEMO)

if [[ $MODO == docker ]]; then
  paso "Construyendo y arrancando con Docker Compose"
  command -v docker >/dev/null || { echo "Instale Docker Engine y el plugin compose."; exit 1; }
  cd "$DESTINO"
  sed -i "s/\"8000:8000\"/\"$PUERTO:8000\"/" docker-compose.yml
  docker compose up -d --build
  docker compose exec -T -e ABC_ADMIN_PASSWORD web python manage.py "${ARGS[@]}"
  cat > /etc/cron.daily/costos-abc-respaldo <<CRON
#!/bin/sh
F=\$(date +%Y%m%d_%H%M); mkdir -p $DESTINO/datos/respaldos/\$F
cd $DESTINO && docker compose exec -T db pg_dump -U abc_salud abc_salud | gzip > $DESTINO/datos/respaldos/\$F/abc.sql.gz
docker run --rm -v costos-abc_media:/m -v $DESTINO/datos/respaldos/\$F:/r alpine tar -czf /r/media.tgz -C /m . 2>/dev/null || true
find $DESTINO/datos/respaldos -mindepth 1 -maxdepth 1 -mtime +30 -exec rm -rf {} +
CRON
else
  paso "Instalando Python y dependencias"
  PY=""; for v in python3.12 python3.13 python3.11 python3; do
    if command -v "$v" >/dev/null && "$v" -c 'import sys;exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)'; then PY="$v"; break; fi; done
  if [[ -z "$PY" ]]; then
    if command -v apt-get >/dev/null; then apt-get update -q && apt-get install -y -q python3 python3-venv; PY=python3
    elif command -v dnf >/dev/null; then dnf install -y python3.12; PY=python3.12; fi
  fi
  [[ -n "$PY" ]] || { echo "Instale Python 3.11-3.13."; exit 1; }
  "$PY" -m venv "$DESTINO/.venv" 2>/dev/null || { apt-get install -y -q python3-venv && "$PY" -m venv "$DESTINO/.venv"; }
  if [[ -d "$DESTINO/wheels" ]]; then
    "$DESTINO/.venv/bin/pip" install -q --no-index --find-links "$DESTINO/wheels" -r "$DESTINO/requirements.txt"
  else
    "$DESTINO/.venv/bin/pip" install -q -r "$DESTINO/requirements.txt"
  fi
  ok "Dependencias instaladas"
  id "$USUARIO_SO" >/dev/null 2>&1 || useradd --system --home "$DESTINO" --shell /usr/sbin/nologin "$USUARIO_SO"
  chown -R "$USUARIO_SO:$USUARIO_SO" "$DESTINO/datos" "$DESTINO/.env"
  mkdir -p "$DESTINO/staticfiles" && chown -R "$USUARIO_SO:$USUARIO_SO" "$DESTINO/staticfiles"

  paso "Base de datos, administrador e institución"
  cd "$DESTINO" && runuser -w ABC_ADMIN_PASSWORD -u "$USUARIO_SO" -- "$DESTINO/.venv/bin/python" manage.py "${ARGS[@]}"

  paso "Servicio systemd y respaldo diario"
  cat > /etc/systemd/system/costos-abc.service <<UNIT
[Unit]
Description=Costos ABC - herramienta de costos en salud
After=network.target postgresql.service

[Service]
User=$USUARIO_SO
WorkingDirectory=$DESTINO
ExecStart=$DESTINO/.venv/bin/gunicorn abc_salud.wsgi:application --bind 0.0.0.0:$PUERTO --workers 3 --timeout 300
Restart=always
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
  cat > /etc/cron.daily/costos-abc-respaldo <<CRON
#!/bin/sh
F=\$(date +%Y%m%d_%H%M); D=$DESTINO/datos/respaldos/\$F; mkdir -p \$D
[ -f $DESTINO/datos/db.sqlite3 ] && $DESTINO/.venv/bin/python -c "import sqlite3;sqlite3.connect('$DESTINO/datos/db.sqlite3').backup(sqlite3.connect('\$D/db.sqlite3'))"
tar -czf \$D/media.tgz -C $DESTINO/datos media
cp $DESTINO/.env \$D/env.txt && chmod 600 \$D/env.txt
# Con PostgreSQL agregue: pg_dump "URL" | gzip > \$D/abc.sql.gz
chown -R $USUARIO_SO:$USUARIO_SO $DESTINO/datos/respaldos
find $DESTINO/datos/respaldos -mindepth 1 -maxdepth 1 -mtime +30 -exec rm -rf {} +
CRON
  if [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    systemctl enable costos-abc && systemctl restart costos-abc
    ok "Servicio costos-abc habilitado (journalctl -u costos-abc para ver el registro)"
  else
    echo "    systemd no está activo en este equipo: inicie manualmente con"
    echo "    cd $DESTINO && runuser -u $USUARIO_SO -- .venv/bin/python manage.py servir --puerto $PUERTO"
  fi
fi
chmod 755 /etc/cron.daily/costos-abc-respaldo
command -v ufw >/dev/null && ufw status | grep -q active && ufw allow "$PUERTO/tcp" >/dev/null || true
unset ABC_ADMIN_PASSWORD

sleep 3
curl -fsS -o /dev/null "http://127.0.0.1:$PUERTO/cuenta/ingresar/" && ok "Servidor respondiendo" || echo "    Revise: journalctl -u costos-abc / docker compose logs"
cat <<FIN

=============================================================
 Instalación terminada
 Acceso:    http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PUERTO/
 Usuario:   $ADMIN
 Datos:     $DESTINO/datos  (respaldo diario, se conservan 30 días)
 Plantilla: $DESTINO/plantillas/Plantilla_Carga_Mensual_ABC.xlsx
=============================================================
FIN
