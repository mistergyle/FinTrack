#!/usr/bin/with-contenv bashio
# Startskript des Add-ons: übersetzt die Add-on-Optionen (/data/options.json) in die
# Umgebungsvariablen der App. Das ersetzt die frühere .env-Datei.
set -e

export FINANZ_DB=/data/finanz.db      # /data ist persistent und Teil der Home-Assistant-Backups
export TRUST_INGRESS=1                # Anmeldung übernimmt Home Assistant (Ingress)

if bashio::config.has_value 'password'; then
    export APP_PASSWORD="$(bashio::config 'password')"
else
    unset APP_PASSWORD
fi
if bashio::config.has_value 'fints_product_id'; then
    export FINTS_PRODUCT_ID="$(bashio::config 'fints_product_id')"
fi
if bashio::config.has_value 'comdirect_client_id'; then
    export COMDIRECT_CLIENT_ID="$(bashio::config 'comdirect_client_id')"
fi
if bashio::config.has_value 'comdirect_client_secret'; then
    export COMDIRECT_CLIENT_SECRET="$(bashio::config 'comdirect_client_secret')"
fi
if bashio::config.true 'cookie_secure'; then
    export COOKIE_SECURE=1
else
    export COOKIE_SECURE=0
fi

LOG_LEVEL="info"
if bashio::config.has_value 'log_level'; then
    LOG_LEVEL="$(bashio::config 'log_level')"
fi

# Keine Geheimnisse ins Log schreiben, nur ob sie gesetzt sind.
bashio::log.info "Starte FinTrack (Direktzugriff mit Passwort: $([ -n "${APP_PASSWORD:-}" ] && echo ja || echo nein)," \
    "FinTS: $([ -n "${FINTS_PRODUCT_ID:-}" ] && echo an || echo aus)," \
    "comdirect-API: $([ -n "${COMDIRECT_CLIENT_ID:-}" ] && echo an || echo aus))"

cd "${FINTRACK_HOME:-/opt/fintrack}"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level "${LOG_LEVEL}"
