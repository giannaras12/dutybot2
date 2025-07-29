import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed, ButtonStyle
from discord.ui import View, Button
import asyncio
import json
from datetime import datetime, timedelta
import random
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Duty Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- Configuration ---
AUTHORIZED_MODS_FILE = "authorized_mods.json"
ACTIVE_DUTIES = {}
MAX_DUTY_DURATION = timedelta(hours=12)

MOD_ROLE_ID = 1399148894566354985
ADMIN_ROLE_ID = MOD_ROLE_ID
LOG_CHANNEL_ID = 1399171018630889472

# --- Bot setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
client = bot

# --- File Handling ---
def load_authorized_mods():
    try:
        with open(AUTHORIZED_MODS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_authorized_mods(mods):
    with open(AUTHORIZED_MODS_FILE, 'w') as f:
        json.dump(mods, f)

authorized_mods = load_authorized_mods()

# --- Checks ---
def is_admin(interaction: Interaction):
    return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)

def is_authorized_mod(user_id: int):
    return user_id in authorized_mods

# --- Reminder View ---
class ReminderView(View):
    def __init__(self, user_id):
        super().__init__(timeout=120)  # 2 minutes
        self.user_id = user_id
        self.responded = False

    @discord.ui.button(label="Continue Duty", style=ButtonStyle.blurple)
    async def continue_duty(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot respond to this duty.", ephemeral=True)
        self.responded = True
        duty = ACTIVE_DUTIES.get(self.user_id)
        if duty:
            duty['last_continue'] = datetime.utcnow()
            duty['continues'] += 1
            await send_log_embed("Duty Continued", interaction.user, {
                "User": f"{interaction.user} ({interaction.user.id})",
                "Continue Time": datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'),
                "Continue Count": duty['continues'],
                "Total Duration": str(datetime.utcnow() - duty['start_time'])[:-7]
            })
        await interaction.response.send_message("Duty continued.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="End Duty", style=ButtonStyle.danger)
    async def end_duty(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You cannot end this duty.", ephemeral=True)
        self.responded = True
        await end_duty_session(interaction.user, auto=False)
        await interaction.response.send_message("Duty ended.", ephemeral=True)
        self.stop()

# --- Commands ---
@tree.command(name="addmod", description="Add a moderator who can use duty commands (Admin only)")
async def addmod(interaction: Interaction, user_id: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
    try:
        uid = int(user_id)
        if uid not in authorized_mods:
            authorized_mods.append(uid)
            save_authorized_mods(authorized_mods)
            await interaction.response.send_message(f"User ID `{uid}` added as authorized mod.", ephemeral=True)
        else:
            await interaction.response.send_message(f"User ID `{uid}` is already authorized.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("Invalid user ID.", ephemeral=True)

@tree.command(name="removemod", description="Remove a moderator's duty command access (Admin only)")
async def removemod(interaction: Interaction, user_id: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
    try:
        uid = int(user_id)
        if uid in authorized_mods:
            authorized_mods.remove(uid)
            save_authorized_mods(authorized_mods)
            await interaction.response.send_message(f"User ID `{uid}` removed from authorized mods.", ephemeral=True)
        else:
            await interaction.response.send_message(f"User ID `{uid}` is not in the list.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("Invalid user ID.", ephemeral=True)

@tree.command(name="viewmods", description="View all authorized moderator IDs (Admin only)")
async def viewmods(interaction: Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    embed = Embed(title="Authorized Moderators", color=discord.Color.orange())
    if not authorized_mods:
        embed.description = "No moderators added yet."
    else:
        for mod_id in authorized_mods:
            try:
                user = await bot.fetch_user(mod_id)
                embed.add_field(name=f"{user}", value=f"ID: {mod_id}", inline=False)
            except:
                embed.add_field(name="Unknown User", value=f"ID: {mod_id}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="viewduties", description="View all current active duties (Admin only)")
async def viewduties(interaction: Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    embed = discord.Embed(title="Active Duties", color=discord.Color.teal())
    if not ACTIVE_DUTIES:
        embed.description = "There are no active duties."
    else:
        for user_id, data in ACTIVE_DUTIES.items():
            embed.add_field(
                name=f"{data['user']} (ID: {user_id})",
                value=f"Start: {data['start_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="dutystart", description="Start your duty shift and begin receiving reminders")
async def dutystart(interaction: Interaction):
    if not is_authorized_mod(interaction.user.id):
        return await interaction.response.send_message("You are not authorized to start duty.", ephemeral=True)
    if interaction.user.id in ACTIVE_DUTIES:
        return await interaction.response.send_message("You are already on duty.", ephemeral=True)

    ACTIVE_DUTIES[interaction.user.id] = {
        "user": interaction.user,
        "start_time": datetime.utcnow(),
        "last_continue": datetime.utcnow(),
        "continues": 0
    }

    embed = Embed(
        title="Duty Started",
        description=f"{interaction.user.mention} started their duty shift.",
        color=discord.Color.green()
    )
    embed.add_field(name="User", value=interaction.user.name)
    embed.add_field(name="User ID", value=str(interaction.user.id))
    embed.add_field(name="Start Time", value=datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'))

    await interaction.response.send_message(embed=embed, ephemeral=True)

    await send_log_embed("Duty Started", interaction.user, {
        "User": f"{interaction.user} ({interaction.user.id})",
        "Start Time": datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p')
    })

    await schedule_reminder(interaction.user)

@tree.command(name="endduty", description="End your current duty shift")
async def endduty(interaction: Interaction):
    if interaction.user.id not in ACTIVE_DUTIES:
        return await interaction.response.send_message("You are not on duty.", ephemeral=True)

    await end_duty_session(interaction.user, auto=False)
    await interaction.response.send_message("Duty ended.", ephemeral=True)

# --- Reminder Logic ---
async def schedule_reminder(user):
    await asyncio.sleep(random.randint(1200, 1800))  # 3–5 minutes
    if user.id not in ACTIVE_DUTIES:
        return

    view = ReminderView(user.id)
    embed = Embed(
        title="Duty Reminder",
        description=f"{user.mention}, you are currently on duty. Please confirm.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Reminder", value=f"#{ACTIVE_DUTIES[user.id]['continues'] + 1}")
    embed.add_field(name="Time", value=datetime.utcnow().strftime('%H:%M:%S'))

    try:
        await user.send(embed=embed, view=view)
        await send_log_embed("Reminder Sent", user, {
            "User": f"{user} ({user.id})",
            "Reminder Time": datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'),
            "Reminder #": ACTIVE_DUTIES[user.id]['continues'] + 1
        })
    except Exception as e:
        print(f"Failed to DM user {user}: {e}")
        return

    await view.wait()

    if not view.responded:
        await end_duty_session(user, auto=True, reason="No response to reminder")
    elif user.id in ACTIVE_DUTIES:
        total_time = datetime.utcnow() - ACTIVE_DUTIES[user.id]['start_time']
        if total_time >= MAX_DUTY_DURATION:
            await end_duty_session(user, auto=True, reason="12-hour limit reached")
        else:
            await schedule_reminder(user)

async def end_duty_session(user, auto=True, reason="No response"):
    if user.id not in ACTIVE_DUTIES:
        return

    duty = ACTIVE_DUTIES.pop(user.id)
    embed = Embed(
        title="Duty Auto-Ended" if auto else "Duty Ended",
        color=discord.Color.red()
    )
    embed.add_field(name="User", value=f"{user} ({user.id})")
    embed.add_field(name="Start Time", value=duty['start_time'].strftime('%A, %d %B %Y %H:%M %p'))
    embed.add_field(name="End Time", value=datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'))
    embed.add_field(name="Total Duration", value=str(datetime.utcnow() - duty['start_time'])[:-7])
    embed.add_field(name="Times Continued", value=str(duty['continues']))
    if auto:
        embed.add_field(name="Reason", value=reason)

    # Log the end to the log channel
    await send_log_embed(embed=embed)

    # If auto-ended, DM the user
    if auto:
        try:
            dm = Embed(
                title="Duty Auto-Ended",
                description="Your duty was automatically ended.",
                color=discord.Color.red()
            )
            dm.add_field(name="Reason", value=reason, inline=False)
            dm.add_field(name="Total Duration", value=str(datetime.utcnow() - duty['start_time'])[:-7])
            await user.send(embed=dm)
        except Exception as e:
            print(f"Failed to DM user {user}: {e}")



# --- Log Embed Sender ---
async def send_log_embed(title=None, user=None, fields=None, embed=None):
    channel = client.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    if embed is None:
        embed = Embed(title=title, color=discord.Color.gold())
        if fields:
            for k, v in fields.items():
                embed.add_field(name=k, value=v, inline=False)
    await channel.send(embed=embed)

# --- Events ---
@bot.event
async def on_ready():
    await tree.sync()
    print(f"Bot connected as {bot.user}")

# --- Launch ---
if __name__ == '__main__':
    import os
    TOKEN = os.getenv("DISCORD_TOKEN")
    keep_alive()
    bot.run(TOKEN)
