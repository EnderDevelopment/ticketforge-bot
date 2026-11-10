# TicketForge Discord Bot

TicketForge is a Discord bot for Roblox store support, with private ticket creation, staff claim and access controls, ticket transcripts, store FAQ replies, server utility commands, and a configurable text-command prefix.

## Install

1. Install Python 3.10 or later.
2. Download or clone the project and open a terminal in its folder.
3. Install the dependencies with `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and set `DISCORD_TOKEN` to your Discord bot token. Optionally set `DATABASE_PATH` to choose where the SQLite database is stored.
5. In the Discord Developer Portal, enable the Message Content intent for the bot. Invite it to your server with the permissions it needs to create and manage ticket channels, view and send messages, manage messages, and send transcripts.
6. Start the bot with `python main.py`.

## Use

1. In your server, run `/ticketsetup` and select a ticket category, staff role, and transcript log channel.
2. Run `/ticketpanel` in the channel where members should open tickets. Members use its button and complete the form to create a private Roblox store support ticket.
3. Staff can use the ticket’s Claim button. The ticket opener or staff can close it with the Close button or `/ticketclose`. On close, the bot creates a text transcript and sends it to the configured log channel; if that channel is unavailable, it tries to DM the person closing the ticket. The ticket channel is then deleted.
4. Use `/ticketadd` and `/ticketremove` within an active ticket to manage access. The opener or staff can add a member; staff can remove a non-opener.
5. Use `/setprefix` to set the server’s text-command prefix to 1–5 non-space characters. The default is `!`; the available text commands are `ping` and `help`.
6. Use `/help`, `/faq`, and `/ping` for command help, store guidance, and latency. The bot also replies to common purchase, delivery, and refund questions, with a 30-second per-user, per-channel cooldown. Moderators can use `/clear`, `/kick`, and `/ban` when they have the required Discord permissions.

---

More Discord bot projects by EnderDevelopment: https://github.com/EnderDevelopment

Project page: https://dash.enderdevelopment.com/community/1082ccca-8eff-4ddc-9ef6-bff6caeb0e53?utm_source=github&utm_medium=readme&utm_campaign=auto_publish&utm_content=1082ccca-8eff-4ddc-9ef6-bff6caeb0e53
