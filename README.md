# Capstone1

Tools LP analysis buat Robinhood Chain. Daftar lengkap, gimana nyambungnya, dan agent
orchestrator-nya: **[TOOLS.md](TOOLS.md)**.

| Folder | Isinya |
| --- | --- |
| [`rh_wallet_finder/`](rh_wallet_finder/README.md) | LP analysis wallet data (Uniswap V4, Robinhood Chain) + nyambung ke RH Smart LP Finder |
| [`rh_wallet_finder/web/`](rh_wallet_finder/web) | Website widget: etalase read-only di Vercel |
| [`agent-office/`](agent-office/README.md) | Guild Hall: semua sesi Claude Code jadi karakter, bisa dicek & disuruh dari HP (Tailscale) |
| [`power-meter/`](power-meter/README.md) | Watt Mac mini real time (total, CPU, GPU), kWh & biaya per hari, dibuka dari HP lewat Tailscale |
| [`lp_call_bot/`](lp_call_bot/README.md) | Bot Telegram: blast call open / close / reentry LP ke follower |
| [`orchestrator/`](TOOLS.md#orchestrator-satu-agent-buat-semuanya) | Agent Claude yang ngejalanin tools di atas |
| [`archive/`](TOOLS.md#arsip-udah-nggak-dipakai) | Vault Tracker + app Hospital (Capstone Module 1), udah nggak dipakai |
