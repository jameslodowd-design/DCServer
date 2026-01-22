from dotenv import load_dotenv
import os
load_dotenv()

import discord
from discord.ext import commands
import asyncio
import random
from datetime import datetime, timedelta
import re
import os
import webserver


# ---------------------- BOT SETUP ----------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=None, intents=intents)

STAFF_ROLE_ID = 1434277044312735754
GUILD_ID = 1434251155856293910
TRANSCRIPT_LOG_CHANNEL_ID = 1434285508535652573
MOD_LOG_CHANNEL_ID = 1463991098430066733

# ---------------------- TICKET COUNTER (PERSISTENT) ----------------------
COUNTER_FILE = "ticket_counter.txt"

def load_ticket_counter():
    if not os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "w") as f:
            f.write("0")
        return 0
    with open(COUNTER_FILE, "r") as f:
        return int(f.read().strip() or "0")

def save_ticket_counter(value):
    with open(COUNTER_FILE, "w") as f:
        f.write(str(value))

ticket_counter = load_ticket_counter()

# ---------------------- MODERATION CONFIG ----------------------
BAD_WORDS = {"nigger", "nigga", "paki", "cunt"}  # put real words here
NSFW_WORDS = {"porn", "sex"}  # put real words here

INVITE_REGEX = re.compile(r"(discord\.gg/|discord\.com/invite/)", re.IGNORECASE)
LINK_REGEX = re.compile(r"https?://", re.IGNORECASE)

SPAM_WINDOW_SECONDS = 7
SPAM_MAX_MESSAGES = 5
SPAM_MAX_MENTIONS = 6

user_message_history = {}  # user_id: list of timestamps

def is_staff(member: discord.Member) -> bool:
    return any(r.id == STAFF_ROLE_ID for r in member.roles)

async def log_moderation(guild: discord.Guild, title: str, description: str, color: discord.Color = discord.Color.red()):
    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.utcnow()
    await channel.send(embed=embed)

# ---------------------- GIVEAWAY SYSTEM ----------------------
giveaways = {}

class GiveawayModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create a Giveaway")

        self.duration = discord.ui.TextInput(label="Duration (e.g. 1m, 10m, 1h)", required=True)
        self.winners = discord.ui.TextInput(label="Number of Winners", default="1", required=True)
        self.prize = discord.ui.TextInput(label="Prize", required=True)

        self.add_item(self.duration)
        self.add_item(self.winners)
        self.add_item(self.prize)

    async def on_submit(self, interaction: discord.Interaction):
        duration_str = self.duration.value
        winners_count = int(self.winners.value)
        prize = self.prize.value

        unit = duration_str[-1]
        value = int(duration_str[:-1])
        seconds = value * 60 if unit == "m" else value * 3600 if unit == "h" else value

        end_time = datetime.utcnow() + timedelta(seconds=seconds)
        formatted_end = end_time.strftime("%d %B %Y %H:%M")

        giveaway_id = str(interaction.id)
        giveaways[giveaway_id] = {
            "entries": [],
            "message": None,
            "winners": winners_count,
            "prize": prize,
            "end_time": end_time,
            "duration": duration_str,
            "host": interaction.user.mention
        }

        embed = discord.Embed(
            title=f"{prize}!",
            description=(
                f"**Ends:** in {duration_str} ({formatted_end})\n"
                f"**Hosted by:** {interaction.user.mention}\n"
                f"**Entries:** 0\n"
                f"**Winners:** {winners_count}"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Click 🍩 to enter!")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🍩", style=discord.ButtonStyle.primary, custom_id=giveaway_id))

        await interaction.response.send_message("Giveaway created!", ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)
        giveaways[giveaway_id]["message"] = msg

        await asyncio.sleep(seconds)

        entries = giveaways[giveaway_id]["entries"]
        if not entries:
            await msg.reply("No one joined the giveaway 😢")
        else:
            winners = random.sample(entries, min(winners_count, len(entries)))
            mentions = ", ".join(w.mention for w in winners)
            await msg.reply(f"🎉 Congratulations {mentions}! You won **{prize}**!")

@bot.tree.command(name="giveaway", description="Create a giveaway")
async def giveaway(interaction: discord.Interaction):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        return await interaction.response.send_message("Only staff members can create giveaways.", ephemeral=True)
    await interaction.response.send_modal(GiveawayModal())

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data["custom_id"]
        if custom_id in giveaways:
            giveaway = giveaways[custom_id]
            user = interaction.user

            if user in giveaway["entries"]:
                return await interaction.response.send_message("You've already joined this giveaway!", ephemeral=True)

            giveaway["entries"].append(user)
            await interaction.response.send_message("You're in! 🍩", ephemeral=True)

            msg = giveaway["message"]
            embed = msg.embeds[0]
            embed.description = (
                f"**Ends:** in {giveaway['duration']} ({giveaway['end_time'].strftime('%d %B %Y %H:%M')})\n"
                f"**Hosted by:** {giveaway['host']}\n"
                f"**Entries:** {len(giveaway['entries'])}\n"
                f"**Winners:** {giveaway['winners']}"
            )
            await msg.edit(embed=embed)

# ---------------------- TRANSCRIPT GENERATOR ----------------------
async def generate_transcript(channel: discord.TextChannel, reason: str = None, closed_by: str = None) -> discord.File:
    lines = []

    if reason:
        lines.append("=== Ticket Closed With Reason ===")
        lines.append(f"Reason: {reason}")
        lines.append(f"Closed by: {closed_by}")
        lines.append("=================================\n")

    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content if msg.content else ""
        if msg.attachments:
            attachments = " ".join(a.url for a in msg.attachments)
            content = f"{content} [Attachments: {attachments}]".strip()
        lines.append(f"[{timestamp}] {author}: {content}")

    text = "\n".join(lines) if lines else "No messages in this ticket."
    filename = f"transcript-{channel.name}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(text)

    return discord.File(filename, filename=filename)

# ---------------------- CLOSE WITH REASON MODAL ----------------------
class CloseReasonModal(discord.ui.Modal, title="Close Ticket With Reason"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="Explain why this ticket is being closed...",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        log_channel = interaction.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)

        topic = interaction.channel.topic
        user_id = int(topic.replace("owner:", "").strip()) if topic else None
        user = interaction.guild.get_member(user_id) if user_id else None

        transcript_file = await generate_transcript(
            interaction.channel,
            reason=self.reason.value,
            closed_by=interaction.user.mention
        )

        embed = discord.Embed(
            title="📄 Ticket Closed With Reason",
            description=(
                f"**Channel:** {interaction.channel.name}\n"
                f"**Closed by:** {interaction.user.mention}\n"
                f"**Reason:** {self.reason.value}"
            ),
            color=discord.Color.red()
        )

        await log_channel.send(embed=embed, file=transcript_file)

        try:
            os.remove(transcript_file.fp.name)
        except:
            pass

        if user:
            try:
                await user.send(
                    "Your ticket has been closed.\n\n"
                    f"Reason: {self.reason.value}\n\n"
                    "If you need more help, feel free to open another ticket."
                )
            except:
                pass

        await interaction.response.send_message("Ticket closed with reason.", ephemeral=True)
        await asyncio.sleep(1)
        await interaction.channel.delete()

# ---------------------- CLOSE TICKET VIEW ----------------------
class CloseTicket(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket and generating transcript...", ephemeral=True)

        topic = interaction.channel.topic
        user_id = int(topic.replace("owner:", "").strip()) if topic else None
        user = interaction.guild.get_member(user_id) if user_id else None

        log_channel = interaction.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)
        transcript_file = await generate_transcript(interaction.channel)

        embed = discord.Embed(
            title="📄 Ticket Transcript",
            description=f"Channel: {interaction.channel.name}\nClosed by: {interaction.user.mention}",
            color=discord.Color.orange()
        )

        await log_channel.send(embed=embed, file=transcript_file)

        try:
            os.remove(transcript_file.fp.name)
        except:
            pass

        if user:
            try:
                await user.send(
                    "Your ticket has been closed.\n\n"
                    "If you need more help, feel free to open another one."
                )
            except:
                pass

        await asyncio.sleep(1)
        await interaction.channel.delete()

    @discord.ui.button(label="Close With Reason", style=discord.ButtonStyle.primary)
    async def close_with_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseReasonModal())

# ---------------------- TICKET DROPDOWN ----------------------
class TicketDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Partnership Tickets", emoji="🧩"),
            discord.SelectOption(label="Giveaway Claim", emoji="🎉"),
            discord.SelectOption(label="Buy/Sell Spawners", emoji="🪙"),
            discord.SelectOption(label="Support", emoji="🛠️")
        ]
        super().__init__(placeholder="Select a ticket type...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        global ticket_counter

        ticket_counter += 1
        save_ticket_counter(ticket_counter)

        ticket_id = str(ticket_counter).zfill(3)

        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)

        partnership_cat = guild.get_channel(1434287092476678246)
        giveaway_cat = guild.get_channel(1434286986428153856)
        spawner_cat = guild.get_channel(1434287282013077577)
        support_cat = guild.get_channel(1434285511526191214)

        selected = self.values[0]

        if selected == "Partnership Tickets":
            category = partnership_cat
        elif selected == "Giveaway Claim":
            category = giveaway_cat
        elif selected == "Buy/Sell Spawners":
            category = spawner_cat
        else:
            category = support_cat

        channel = await guild.create_text_channel(
            name=f"ticket-{ticket_id}",
            category=category,
            topic=f"owner:{interaction.user.id}",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True),
                staff_role: discord.PermissionOverwrite(view_channel=True)
            }
        )

        await channel.send(
            f"<@&{STAFF_ROLE_ID}> A new ticket has been opened.",
            allowed_mentions=discord.AllowedMentions(roles=True)
        )

        await interaction.response.send_message(
            f"Your ticket has been created in **{category.name}**: {channel.mention}",
            ephemeral=True
        )

        embed = discord.Embed(
            title=f"🎫 Ticket #{ticket_id}",
            description=f"**User:** {interaction.user.mention}\n"
                        f"**Topic:** {selected}\n\n"
                        f"A staff member will assist you shortly.",
            color=discord.Color.blue()
        )

        await channel.send(embed=embed, view=CloseTicket())

# ---------------------- TICKET PANEL ----------------------
class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

@bot.tree.command(name="ticketpanel", description="Create the ticket panel")
async def ticketpanel(interaction: discord.Interaction):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        return await interaction.response.send_message("Only staff can create the ticket panel.", ephemeral=True)

    embed = discord.Embed(
        title="🎫 Support Tickets",
        description=(
            "Select an option from the dropdown below to open a ticket.\n\n"
            "**Ticket Types:**\n"
            "🧩 Partnership Tickets\n"
            "🎉 Giveaway Claim\n"
            "🪙 Buy/Sell Spawners\n"
            "🛠️ Support\n\n"
            "A staff member will assist you shortly."
        ),
        color=discord.Color.gold()
    )

    await interaction.channel.send(embed=embed, view=TicketPanel())
    await interaction.response.send_message("Ticket panel created.", ephemeral=True)

# ---------------------- MODERATION: MESSAGE HANDLER ----------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if is_staff(message.author):
        await bot.process_commands(message)
        return

    content_lower = message.content.lower()

    # Bad-word filter
    if any(word in content_lower for word in BAD_WORDS):
        try:
            await message.delete()
        except:
            pass
        await log_moderation(
            message.guild,
            "Bad Word Detected",
            f"User: {message.author.mention}\nChannel: {message.channel.mention}\nMessage: {message.content}"
        )
        try:
            await message.author.send("Your message was removed for inappropriate language.")
        except:
            pass
        return

    # NSFW filter
    if any(word in content_lower for word in NSFW_WORDS):
        try:
            await message.delete()
        except:
            pass
        await log_moderation(
            message.guild,
            "NSFW Content Detected",
            f"User: {message.author.mention}\nChannel: {message.channel.mention}\nMessage: {message.content}"
        )
        try:
            await message.author.send("Your message was removed for inappropriate content.")
        except:
            pass
        return

    # Invite / link filter
    if INVITE_REGEX.search(message.content) or LINK_REGEX.search(message.content):
        try:
            await message.delete()
        except:
            pass
        await log_moderation(
            message.guild,
            "Link/Invite Blocked",
            f"User: {message.author.mention}\nChannel: {message.channel.mention}\nMessage: {message.content}"
        )
        try:
            await message.author.send("Links and invites are not allowed here.")
        except:
            pass
        return

    # Spam tracking
    now = datetime.utcnow().timestamp()
    history = user_message_history.get(message.author.id, [])
    history = [t for t in history if now - t <= SPAM_WINDOW_SECONDS]
    history.append(now)
    user_message_history[message.author.id] = history

    if len(history) >= SPAM_MAX_MESSAGES or len(message.mentions) >= SPAM_MAX_MENTIONS:
        try:
            await message.delete()
        except:
            pass
        try:
            await message.author.timeout(timedelta(minutes=5), reason="Spam detected")
        except:
            pass
        await log_moderation(
            message.guild,
            "Spam Detected",
            f"User: {message.author.mention}\nChannel: {message.channel.mention}\nMessage: {message.content}"
        )
        try:
            await message.author.send("You have been temporarily timed out for spamming.")
        except:
            pass
        return

    await bot.process_commands(message)

# ---------------------- MODERATION COMMANDS ----------------------
@bot.tree.command(name="warn", description="Warn a user")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    await interaction.response.send_message(f"{member.mention} has been warned.", ephemeral=True)
    try:
        await member.send(f"You have been warned in {interaction.guild.name}.\nReason: {reason}")
    except:
        pass

    await log_moderation(
        interaction.guild,
        "User Warned",
        f"User: {member.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
        color=discord.Color.orange()
    )

@bot.tree.command(name="mute", description="Timeout a user")
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    duration = timedelta(minutes=minutes)
    try:
        await member.timeout(duration, reason=reason)
    except:
        return await interaction.response.send_message("Failed to timeout user.", ephemeral=True)

    await interaction.response.send_message(f"{member.mention} has been timed out for {minutes} minutes.", ephemeral=True)
    try:
        await member.send(f"You have been timed out in {interaction.guild.name} for {minutes} minutes.\nReason: {reason}")
    except:
        pass

    await log_moderation(
        interaction.guild,
        "User Timed Out",
        f"User: {member.mention}\nBy: {interaction.user.mention}\nDuration: {minutes} minutes\nReason: {reason}",
        color=discord.Color.orange()
    )

@bot.tree.command(name="kick", description="Kick a user")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    try:
        await member.kick(reason=reason)
    except:
        return await interaction.response.send_message("Failed to kick user.", ephemeral=True)

    await interaction.response.send_message(f"{member.mention} has been kicked.", ephemeral=True)
    try:
        await member.send(f"You have been kicked from {interaction.guild.name}.\nReason: {reason}")
    except:
        pass

    await log_moderation(
        interaction.guild,
        "User Kicked",
        f"User: {member.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
        color=discord.Color.orange()
    )

@bot.tree.command(name="ban", description="Ban a user")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    try:
        await member.ban(reason=reason)
    except:
        return await interaction.response.send_message("Failed to ban user.", ephemeral=True)

    await interaction.response.send_message(f"{member.mention} has been banned.", ephemeral=True)
    try:
        await member.send(f"You have been banned from {interaction.guild.name}.\nReason: {reason}")
    except:
        pass

    await log_moderation(
        interaction.guild,
        "User Banned",
        f"User: {member.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
        color=discord.Color.red()
    )

@bot.tree.command(name="purge", description="Delete a number of messages in this channel")
async def purge(interaction: discord.Interaction, amount: int):
    if not is_staff(interaction.user):
        return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount + 1)
    await interaction.followup.send(f"Deleted {len(deleted) - 1} messages.", ephemeral=True)

    await log_moderation(
        interaction.guild,
        "Messages Purged",
        f"Channel: {interaction.channel.mention}\nBy: {interaction.user.mention}\nAmount: {len(deleted) - 1}",
        color=discord.Color.dark_grey()
    )

# ---------------------- READY EVENT ----------------------
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print(f"Bot is online as {bot.user}")

# ---------------------- RUN BOT ----------------------
webserver.keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))


