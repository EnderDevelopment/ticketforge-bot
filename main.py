import asyncio
import io
import os
import re
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
DB_PATH = os.getenv("DATABASE_PATH", "bot.sqlite3")
DEFAULT_PREFIX = "!"


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with db() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS guild_settings (guild_id INTEGER PRIMARY KEY, prefix TEXT NOT NULL DEFAULT '!')")
        connection.execute("CREATE TABLE IF NOT EXISTS ticket_settings (guild_id INTEGER PRIMARY KEY, category_id INTEGER, staff_role_id INTEGER, log_channel_id INTEGER)")
        connection.execute("CREATE TABLE IF NOT EXISTS tickets (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, opener_id INTEGER NOT NULL, claimed_by INTEGER, created_at TEXT NOT NULL)")


def get_prefix(guild_id):
    if guild_id is None:
        return DEFAULT_PREFIX
    with db() as connection:
        row = connection.execute("SELECT prefix FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["prefix"] if row else DEFAULT_PREFIX


async def prefix_for(bot, message):
    return commands.when_mentioned_or(get_prefix(message.guild.id if message.guild else None))(bot, message)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=prefix_for, intents=intents, help_command=None)


def ticket_settings(guild_id):
    with db() as connection:
        return connection.execute("SELECT * FROM ticket_settings WHERE guild_id = ?", (guild_id,)).fetchone()


def is_ticket_opener(channel_id, user_id):
    with db() as connection:
        row = connection.execute("SELECT opener_id FROM tickets WHERE channel_id = ?", (channel_id,)).fetchone()
    return row is not None and row["opener_id"] == user_id


def staff_check(member, settings):
    if member.guild_permissions.manage_channels or member.guild_permissions.administrator:
        return True
    return bool(settings and settings["staff_role_id"] and any(role.id == settings["staff_role_id"] for role in member.roles))


def clean_name(value):
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return value[:40] or "user"


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open a ticket", style=discord.ButtonStyle.primary, custom_id="tickets:open", emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
        settings = ticket_settings(guild.id)
        if not settings:
            return await interaction.response.send_message("Ticket system is not configured. Ask an administrator to run `/ticketsetup`.", ephemeral=True)
        with db() as connection:
            existing = connection.execute("SELECT channel_id FROM tickets WHERE guild_id = ? AND opener_id = ?", (guild.id, interaction.user.id)).fetchone()
        if existing and guild.get_channel(existing["channel_id"]):
            return await interaction.response.send_message(f"You already have a ticket: <#{existing['channel_id']}>", ephemeral=True)
        await interaction.response.send_modal(StoreTicketModal())


class StoreTicketModal(discord.ui.Modal, title="Roblox store support"):
    request_type = discord.ui.TextInput(label="Reason: order, purchase, or question", max_length=40)
    roblox_user = discord.ui.TextInput(label="Your Roblox username", max_length=32)
    item = discord.ui.TextInput(label="Item or order details", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        settings = ticket_settings(guild.id) if guild else None
        if guild is None or settings is None:
            return await interaction.response.send_message("Ticket configuration is unavailable. Please contact staff.", ephemeral=True)
        with db() as connection:
            existing = connection.execute("SELECT channel_id FROM tickets WHERE guild_id = ? AND opener_id = ?", (guild.id, interaction.user.id)).fetchone()
        if existing and guild.get_channel(existing["channel_id"]):
            return await interaction.response.send_message(f"You already have a ticket: <#{existing['channel_id']}>", ephemeral=True)
        bot_member = guild.me
        if bot_member is None:
            return await interaction.response.send_message("The bot could not resolve its server member. Try again shortly.", ephemeral=True)
        category = guild.get_channel(settings["category_id"]) if settings["category_id"] else None
        if category and not isinstance(category, discord.CategoryChannel):
            category = None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        role = guild.get_role(settings["staff_role_id"]) if settings["staff_role_id"] else None
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        channel = await guild.create_text_channel(
            f"order-{clean_name(interaction.user.name)}", category=category, overwrites=overwrites,
            topic=f"Store ticket for {interaction.user} ({interaction.user.id})",
        )
        with db() as connection:
            connection.execute("INSERT INTO tickets(channel_id, guild_id, opener_id, created_at) VALUES (?, ?, ?, ?)", (channel.id, guild.id, interaction.user.id, datetime.now(timezone.utc).isoformat()))
        embed = discord.Embed(
            title="Roblox store support",
            description=(f"Welcome {interaction.user.mention}! A staff member will help with your request.\n\n"
                         f"**Request:** {self.request_type.value}\n**Roblox username:** {self.roblox_user.value}\n"
                         f"**Item/order details:**\n{self.item.value}\n\n"
                         "Keep all purchase discussion in this ticket and never share your Roblox password or account credentials."),
            color=discord.Color.blurple(),
        )
        await channel.send(content=role.mention if role else None, embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Your support ticket is ready: {channel.mention}", ephemeral=True)


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="tickets:claim", emoji="🧑‍💼")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = ticket_settings(interaction.guild.id)
        if not staff_check(interaction.user, settings):
            return await interaction.response.send_message("Only ticket staff can claim tickets.", ephemeral=True)
        with db() as connection:
            cursor = connection.execute("UPDATE tickets SET claimed_by = ? WHERE channel_id = ?", (interaction.user.id, interaction.channel_id))
        if cursor.rowcount == 0:
            return await interaction.response.send_message("This is not an active ticket.", ephemeral=True)
        await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="tickets:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction, interaction.user)


async def close_ticket(interaction, actor):
    guild = interaction.guild
    channel = interaction.channel
    if guild is None:
        return await interaction.response.send_message("Tickets can only be closed in a server.", ephemeral=True)
    with db() as connection:
        ticket = connection.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel.id,)).fetchone()
    if not ticket:
        return await interaction.response.send_message("This channel is not an active ticket.", ephemeral=True)
    settings = ticket_settings(guild.id)
    if actor.id != ticket["opener_id"] and not staff_check(actor, settings):
        return await interaction.response.send_message("Only the ticket opener or ticket staff can close this ticket.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    lines = [
        f"Transcript for #{channel.name}",
        f"Guild: {guild.name} ({guild.id})",
        f"Opened by: {ticket['opener_id']}",
        f"Closed by: {actor} ({actor.id})",
        "",
    ]
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        text = message.content or ""
        if message.attachments:
            text += " " + " ".join(item.url for item in message.attachments)
        if message.embeds:
            text += " [embed]"
        lines.append(f"[{timestamp}] {message.author} ({message.author.id}): {text}")
    log_channel = guild.get_channel(settings["log_channel_id"]) if settings and settings["log_channel_id"] else None
    try:
        transcript = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"{channel.name}-transcript.txt")
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"Ticket `{channel.name}` closed by {actor.mention}.", file=transcript)
        else:
            await actor.send(f"Transcript for ticket `{channel.name}` in **{guild.name}**.", file=transcript)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"Could not deliver ticket transcript for {channel.id}: {error}")
    with db() as connection:
        connection.execute("DELETE FROM tickets WHERE channel_id = ?", (channel.id,))
    await interaction.followup.send("Ticket closed. The channel will be deleted shortly.", ephemeral=True)
    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"Ticket closed by {actor}")
    except discord.HTTPException as error:
        print(f"Could not delete closed ticket {channel.id}: {error}")


@bot.event
async def setup_hook():
    initialize_database()
    bot.add_view(TicketPanel())
    bot.add_view(TicketControls())
    await bot.tree.sync()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.tree.command(name="setprefix", description="Set this server's text command prefix")
@app_commands.describe(prefix="Prefix of 1 to 5 characters")
@app_commands.checks.has_permissions(manage_guild=True)
async def setprefix(interaction: discord.Interaction, prefix: str):
    if not interaction.guild:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    if not 1 <= len(prefix) <= 5 or any(character.isspace() for character in prefix):
        return await interaction.response.send_message("Prefix must be 1 to 5 non-space characters.", ephemeral=True)
    with db() as connection:
        connection.execute("INSERT INTO guild_settings(guild_id, prefix) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET prefix = excluded.prefix", (interaction.guild.id, prefix))
    await interaction.response.send_message(f"Text command prefix set to `{prefix}`.", ephemeral=True)


@bot.tree.command(name="ticketsetup", description="Configure the ticket category, staff role, and transcript log")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(category="Category for new tickets", staff_role="Role allowed to manage tickets", log_channel="Channel receiving transcripts")
async def ticketsetup(interaction: discord.Interaction, category: discord.CategoryChannel, staff_role: discord.Role, log_channel: discord.TextChannel):
    if not interaction.guild:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    with db() as connection:
        connection.execute("INSERT INTO ticket_settings(guild_id, category_id, staff_role_id, log_channel_id) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET category_id = excluded.category_id, staff_role_id = excluded.staff_role_id, log_channel_id = excluded.log_channel_id", (interaction.guild.id, category.id, staff_role.id, log_channel.id))
    await interaction.response.send_message(f"Ticket settings saved. Category: {category.mention}, staff: {staff_role.mention}, logs: {log_channel.mention}.", ephemeral=True)


@bot.tree.command(name="ticketpanel", description="Post the Roblox store support ticket panel in this channel")
@app_commands.checks.has_permissions(manage_guild=True)
async def ticketpanel(interaction: discord.Interaction):
    if not interaction.guild or not ticket_settings(interaction.guild.id):
        return await interaction.response.send_message("Run `/ticketsetup` first.", ephemeral=True)
    await interaction.channel.send(
        embed=discord.Embed(title="Roblox item store support", description="Need help with an order, an item, or a purchase question? Open a private ticket and provide your Roblox username and request details. Never share your password or account credentials.", color=discord.Color.blurple()),
        view=TicketPanel(),
    )
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="ticketadd", description="Give a user access to the current ticket")
@app_commands.describe(user="User to add")
async def ticketadd(interaction: discord.Interaction, user: discord.Member):
    if not interaction.guild:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    with db() as connection:
        active = connection.execute("SELECT 1 FROM tickets WHERE channel_id = ?", (interaction.channel_id,)).fetchone()
    allowed = is_ticket_opener(interaction.channel_id, interaction.user.id) or staff_check(interaction.user, ticket_settings(interaction.guild.id))
    if not active or not allowed:
        return await interaction.response.send_message("You cannot modify this channel's ticket access.", ephemeral=True)
    await interaction.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    await interaction.response.send_message(f"Added {user.mention} to this ticket.")


@bot.tree.command(name="ticketremove", description="Remove a user's access from the current ticket")
@app_commands.describe(user="User to remove")
async def ticketremove(interaction: discord.Interaction, user: discord.Member):
    if not interaction.guild:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    with db() as connection:
        active = connection.execute("SELECT opener_id FROM tickets WHERE channel_id = ?", (interaction.channel_id,)).fetchone()
    if not active or user.id == active["opener_id"] or not staff_check(interaction.user, ticket_settings(interaction.guild.id)):
        return await interaction.response.send_message("Only ticket staff can remove a non-opener from an active ticket.", ephemeral=True)
    await interaction.channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"Removed {user.mention} from this ticket.")


@bot.tree.command(name="ticketclose", description="Close the current ticket and save its transcript")
async def ticketclose(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("Use this command in a server.", ephemeral=True)
    await close_ticket(interaction, interaction.user)


@bot.tree.command(name="ping", description="Show bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong: {round(bot.latency * 1000)} ms", ephemeral=True)


@bot.tree.command(name="help", description="List bot commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot commands", color=discord.Color.blurple())
    embed.add_field(name="Tickets", value="`/ticketsetup`, `/ticketpanel`, `/ticketadd`, `/ticketremove`, `/ticketclose`", inline=False)
    embed.add_field(name="Store FAQ", value="`/faq` shows purchasing, delivery, and refund guidance. The bot also replies automatically to common purchase and delivery questions (with a 30-second per-user channel cooldown).", inline=False)
    embed.add_field(name="Settings and utility", value="`/setprefix`, `/ping`, `/help`", inline=False)
    embed.add_field(name="Text commands", value=f"`{get_prefix(interaction.guild.id if interaction.guild else None)}ping`, `help`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear", description="Delete recent messages in this channel")
@app_commands.describe(amount="Number of messages to delete (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("This command requires a text channel.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(name="kick", description="Kick a server member")
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"Kicked {member.mention}. Reason: {reason}")


@bot.tree.command(name="ban", description="Ban a server member")
@app_commands.describe(member="Member to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"Banned {member.mention}. Reason: {reason}")


FAQ_RESPONSES = {
    "purchase": (
        ("buy", "purchase", "order", "payment", "pay", "kup", "zakup", "zamowien", "zamówien", "platn", "płatn"),
        "**Purchasing FAQ:** Check the store listing and server rules for the current price, payment options, and availability before ordering. For help with a specific purchase, open a private ticket with the item name and order details. Never share your password or payment card details here."
    ),
    "delivery": (
        ("delivery", "deliver", "received", "where is", "when arrive", "dostaw", "otrzym", "gdzie moje", "kiedy dostan"),
        "**Delivery FAQ:** Delivery timing and method depend on the item and the store's current policy; check the listing or order confirmation for details. If your order has not arrived within the stated timeframe, open a private ticket and include your order reference. Never share your Roblox password."
    ),
    "refund": (
        ("refund", "return", "chargeback", "zwrot", "reklamac"),
        "**Refund FAQ:** Refund eligibility depends on the store's published policy and the order status. Please open a private ticket with your order reference so staff can review it. Do not post payment credentials or private account information."
    ),
}
faq_last_reply = {}


def find_faq_response(content):
    normalized = content.casefold()
    for keywords, response in FAQ_RESPONSES.values():
        if any(keyword in normalized for keyword in keywords):
            return response
    return None


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild:
        answer = find_faq_response(message.content)
        key = (message.guild.id, message.channel.id, message.author.id)
        now = asyncio.get_running_loop().time()
        if answer and now - faq_last_reply.get(key, 0) >= 30:
            faq_last_reply[key] = now
            try:
                await message.reply(answer, mention_author=False)
            except (discord.Forbidden, discord.HTTPException) as error:
                print(f"Could not send FAQ response in {message.channel.id}: {error}")
    await bot.process_commands(message)


@bot.tree.command(name="faq", description="Show purchase, delivery, and refund guidance")
async def faq_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Roblox Store FAQ", color=discord.Color.blurple())
    for keywords, response in FAQ_RESPONSES.values():
        title = "Purchasing" if "purchase" in keywords else "Delivery" if "delivery" in keywords else "Refunds"
        embed.add_field(name=title, value=response, inline=False)
    embed.set_footer(text="For order-specific help, open a private support ticket.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command(name="ping")
async def text_ping(ctx):
    await ctx.reply(f"Pong: {round(bot.latency * 1000)} ms")


@bot.command(name="help")
async def text_help(ctx):
    await ctx.reply(f"Use `/help` for the command list. Current prefix: `{get_prefix(ctx.guild.id if ctx.guild else None)}`")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "The bot is missing required permissions."
    else:
        message = "The command failed. Check the bot's permissions and try again."
        print(f"Application command error: {error!r}")
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set your bot token.")

bot.run(TOKEN)
