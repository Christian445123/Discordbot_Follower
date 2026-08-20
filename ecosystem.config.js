'use strict';

module.exports = {
  apps: [
    {
      name:            'follower-bot',
      script:          'bot.py',
      // Zeigt auf das projekteigene venv (siehe README: Setup auf dem Linux-Server).
      interpreter:     './venv/bin/python3',
      cwd:             __dirname,
      instances:       1,
      exec_mode:       'fork',
      autorestart:     true,
      // Vorher gab PM2 nach 10 Fehlversuchen dauerhaft auf ('errored'-Status)
      // und der Bot blieb offline, bis jemand manuell 'pm2 restart' ausfuehrte.
      // Hoher Wert + exponentielles Backoff: PM2 versucht es immer weiter,
      // ohne bei einem kurzen Crash-Loop (z.B. voruebergehender DB-Ausfall
      // beim Start) sofort alle 100ms neu zu starten.
      max_restarts:    1000,
      min_uptime:      '30s',
      exp_backoff_restart_delay: 100,
      // Taeglicher Neustart um Mitternacht (Server-Zeitzone) als zusaetzliches
      // Sicherheitsnetz gegen langsam "einschlafende" Zustaende (z.B. haengende
      // Gateway-Session), unabhaengig von Crashes.
      cron_restart:    '0 0 * * *',
      watch:           false,
      env: {
        PYTHONUNBUFFERED: '1',
      },
      // Separate stdout/stderr files instead of PM2's default ~/.pm2/logs/*.
      // Unser eigener Logger stempelt bereits jede Zeile, daher PM2's --time
      // Prefix weglassen um doppelte Timestamps zu vermeiden.
      out_file:        './logs/out.log',
      error_file:      './logs/error.log',
      merge_logs:      true,
      time:            false,
    },
  ],
};
