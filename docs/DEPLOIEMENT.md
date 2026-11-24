# Mise en production — FATONTINE CASH

Objectif : accueillir les premiers membres sur un serveur sécurisé (HTTPS) avec une base PostgreSQL sauvegardée chaque nuit.

## Option A : serveur Linux (VPS Ubuntu 22.04/24.04)

### 1. Paquets
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx git
```

### 2. Base de données
```bash
sudo -u postgres psql -c "CREATE USER fatontine WITH PASSWORD 'MOT_DE_PASSE_FORT';"
sudo -u postgres psql -c "CREATE DATABASE fatontine OWNER fatontine;"
```

### 3. Application
```bash
sudo useradd -r -m -d /opt/fatontine fatontine
sudo -u fatontine git clone https://github.com/ck526438-lgtm/fatontine-cash.git /opt/fatontine/app
cd /opt/fatontine/app
sudo -u fatontine python3 -m venv /opt/fatontine/venv
sudo -u fatontine /opt/fatontine/venv/bin/pip install -r requirements.txt
sudo mkdir -p /var/lib/fatontine/private_media /var/backups/fatontine
sudo chown -R fatontine: /var/lib/fatontine /var/backups/fatontine
sudo chmod 750 /var/lib/fatontine/private_media
sudo -u fatontine cp .env.example .env && sudo -u fatontine nano .env   # remplir toutes les valeurs
sudo -u fatontine chmod 600 .env
sudo -u fatontine /opt/fatontine/venv/bin/python manage.py migrate
sudo -u fatontine /opt/fatontine/venv/bin/python manage.py collectstatic --noinput
sudo -u fatontine /opt/fatontine/venv/bin/python manage.py init_fatontine --email fatima@VOTRE-DOMAINE
sudo -u fatontine /opt/fatontine/venv/bin/python manage.py check --deploy
```

### 4. Service gunicorn — `/etc/systemd/system/fatontine.service`
```ini
[Unit]
Description=FATONTINE CASH
After=network.target postgresql.service

[Service]
User=fatontine
WorkingDirectory=/opt/fatontine/app
ExecStart=/opt/fatontine/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now fatontine
```

### 5. Nginx + SSL — `/etc/nginx/sites-available/fatontine`
```nginx
server {
    server_name fatontine.ci www.fatontine.ci;
    client_max_body_size 12M;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/fatontine /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d fatontine.ci -d www.fatontine.ci
```
> Les pièces d'identité ne sont **jamais** servies par Nginx : elles restent dans `/var/lib/fatontine/private_media` et passent uniquement par l'application, une fois l'administratrice connectée.

### 6. Sauvegardes (§8) — `sudo crontab -u fatontine -e`
```cron
# Chaque nuit à 2 h : dump PostgreSQL + export JSON, conservés 30 jours
0 2 * * * pg_dump -Fc "postgresql://fatontine:MOT_DE_PASSE@localhost/fatontine" > /var/backups/fatontine/pg-$(date +\%F).dump && find /var/backups/fatontine -name 'pg-*.dump' -mtime +30 -delete
15 2 * * * cd /opt/fatontine/app && /opt/fatontine/venv/bin/python manage.py sauvegarde --garder 30
# Chaque dimanche : copie des pièces d'identité (chiffrées) et purge selon la durée de conservation
30 2 * * 0 tar czf - -C /var/lib/fatontine private_media | gpg --symmetric --batch --passphrase-file /opt/fatontine/.backup-pass -o /var/backups/fatontine/pieces-$(date +\%F).tgz.gpg
45 2 * * 0 cd /opt/fatontine/app && /opt/fatontine/venv/bin/python manage.py purger_pieces
```
Copiez aussi `/var/backups/fatontine` hors du serveur (rclone vers Google Drive, ou stockage objet).

### Restauration
```bash
pg_restore --clean -d "postgresql://fatontine:MOT_DE_PASSE@localhost/fatontine" /var/backups/fatontine/pg-AAAA-MM-JJ.dump
# ou, à partir de l'export JSON sur une base vierge migrée :
python manage.py loaddata /var/backups/fatontine/fatontine-AAAAMMJJ-HHMMSS.json.gz
```
Testez la restauration une fois par mois sur une base de test.

## Option B : Docker Compose
```bash
cp .env.example .env    # remplir ; DATABASE_URL est fournie par docker-compose
export POSTGRES_PASSWORD='MOT_DE_PASSE_FORT'
docker compose up -d --build
docker compose exec web python manage.py init_fatontine --email fatima@VOTRE-DOMAINE
```
Placez ensuite Nginx + certbot devant `127.0.0.1:8000`, comme à l'étape 5.

## Liste de contrôle avant le premier membre
- [ ] `python manage.py check --deploy` n'affiche aucun avertissement
- [ ] Le site s'ouvre en **https://** et le cadenas est valide
- [ ] L'e-mail de réinitialisation arrive bien (SMTP configuré)
- [ ] La 1ʳᵉ sauvegarde nocturne existe et la restauration a été testée
- [ ] Les points du §13 sont validés avec Fatima (frais de gestion, retards, abandons, remboursements, confirmation Wave)
- [ ] La durée de conservation des pièces (Paramètres) est validée et mentionnée dans le consentement
- [ ] La déclaration du traitement de données personnelles auprès de l'ARTCI a été examinée
