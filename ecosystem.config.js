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
      max_restarts:    10,
      min_uptime:      '30s',
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
