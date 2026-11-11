# Capstone1

Tools LP analysis buat Robinhood Chain. Daftar lengkap, gimana nyambungnya, dan agent
orchestrator-nya: **[TOOLS.md](TOOLS.md)**.

| Folder | Isinya |
| --- | --- |
| [`rh_wallet_finder/`](rh_wallet_finder/README.md) | LP analysis wallet data (Uniswap V4, Robinhood Chain) + nyambung ke RH Smart LP Finder |
| [`rh_wallet_finder/web/`](rh_wallet_finder/web) | Website widget: etalase read-only di Vercel |
| [`agent-office/`](agent-office/README.md) | **Agent Office**: semua sesi Claude Code + spesialis yang di-hire jadi orang di kantor pixel; header kesehatan Mac, limit Claude, Money, Eco; semua program lain di repo ini muncul di Server room-nya (HP lewat Tailscale) |
| [`power-meter/`](power-meter/README.md) | Watt Mac mini real time (total, CPU, GPU), kWh & biaya per hari, dibuka dari HP lewat Tailscale |
| [`lp_call_bot/`](lp_call_bot/README.md) | Bot Telegram: blast call open / close / reentry LP ke follower |
| [`rh_terminal/`](rh_terminal/README.md) | **RH Terminal**: pool & portfolio Uniswap V4 Robinhood, entry check, ladder, auto exit (web 8892) |
| [`elysium_terminal/`](elysium_terminal/README.md) | **Elysium Terminal** + [`elysium_wallet_finder/`](elysium_wallet_finder/README.md): versi Elysium dari RH Terminal dan Wallet Finder |
| [`dlmm_terminal/`](dlmm_terminal/README.md) | Solana LP: filter entry pool Meteora DLMM + monitor PnL & auto exit (spec v1) |
| [`rpc_monitor/`](rpc_monitor/README.md) | **RPC Monitor**: semua RPC yang dipakai semua bot, mana yang udah mau limit (kuota, 429, probe), dibuka dari HP (Tailscale) |
| [`fund/`](fund/README.md) | **Fund**: net worth, pengeluaran, saran fund manager + realisasinya, LP auto-track (0 RPC). Terpisah dari tools LP, dibuka dari HP (Tailscale) |
| [`FUND_MANAGER.md`](FUND_MANAGER.md) | Fund manager: sleeves, IPS, workflow harian → tahunan, subagent `fund-manager` |
| [`orchestrator/`](TOOLS.md#orchestrator-satu-agent-buat-semuanya) | Agent Claude yang ngejalanin tools di atas |
| [`archive/`](TOOLS.md#arsip-udah-nggak-dipakai) | Vault Tracker + app Hospital (Capstone Module 1), udah nggak dipakai |
