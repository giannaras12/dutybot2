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
import os

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
POINTS_FILE = "points.json"
ACTIVE_DUTIES = {}
REMINDER_TASKS = {}  # Track reminder tasks to prevent duplicates
MAX_DUTY_DURATION = timedelta(hours=12)

MOD_ROLE_ID = 1386555863728390229
ADMIN_ROLE_ID = MOD_ROLE_ID
LOG_CHANNEL_ID = 1386555864831365197

# --- Bot setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
client = bot

# --- Logging Helper ---
def log_to_console(event_type, user=None, details=None):
    """Log events to console for debugging"""
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    log_message = f"[{timestamp}] {event_type}"
    
    if user:
        log_message += f" - User: {user} (ID: {user.id})"
    
    if details:
        for key, value in details.items():
            log_message += f" | {key}: {value}"
    
    print(log_message)

# --- File Handling ---
def load_authorized_mods():
    try:
        with open(AUTHORIZED_MODS_FILE, 'r') as f:
            data = json.load(f)
            log_to_console("SYSTEM", details={"Action": "Loaded authorized mods", "Count": len(data)})
            return data
    except FileNotFoundError:
        log_to_console("SYSTEM", details={"Action": "Created new authorized mods file"})
        return []

def save_authorized_mods(mods):
    with open(AUTHORIZED_MODS_FILE, 'w') as f:
        json.dump(mods, f)
    log_to_console("SYSTEM", details={"Action": "Saved authorized mods", "Count": len(mods)})

def load_points():
    try:
        with open(POINTS_FILE, 'r') as f:
            data = json.load(f)
            log_to_console("SYSTEM", details={"Action": "Loaded points data", "Users": len(data)})
            return data
    except FileNotFoundError:
        log_to_console("SYSTEM", details={"Action": "Created new points file"})
        return {}

def save_points(points):
    with open(POINTS_FILE, 'w') as f:
        json.dump(points, f)
    log_to_console("SYSTEM", details={"Action": "Saved points data", "Users": len(points)})

points = load_points()
authorized_mods = load_authorized_mods()

# --- Checks ---
def is_admin(interaction: Interaction):
    return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)

def is_authorized_mod(user_id: int):
    return user_id in authorized_mods

# --- Reminder View ---
class ReminderView(View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
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
            
            log_to_console("DUTY_CONTINUED", interaction.user, {
                "Continue Count": duty['continues'],
                "Total Duration": str(datetime.utcnow() - duty['start_time'])[:-7]
            })
            
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

# --- Log Helper ---
async def send_log_embed(title=None, user=None, fields=None, embed=None):
    """Send embed to log channel and print to console"""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        try:
            log_channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except Exception as e:
            log_to_console("LOG_CHANNEL_FETCH_FAILED", details={"Error": str(e)})
            return

    if embed is None:
        embed = Embed(title=title, color=discord.Color.blue())
        if fields:
            for key, value in fields.items():
                embed.add_field(name=key, value=value, inline=False)

    # Log to console
    if user:
        log_to_console(title or "LOG_EVENT", user, fields)
    else:
        log_to_console(title or "LOG_EVENT", details=fields)

    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        log_to_console("LOG_SEND_FAILED", details={"Error": str(e)})

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
            log_to_console("MOD_ADDED", interaction.user, {"Added User ID": uid})
            await interaction.response.send_message(f"User ID {uid} added as authorized mod.", ephemeral=True)
        else:
            await interaction.response.send_message(f"User ID {uid} is already authorized.", ephemeral=True)
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
            log_to_console("MOD_REMOVED", interaction.user, {"Removed User ID": uid})
            await interaction.response.send_message(f"User ID {uid} removed from authorized mods.", ephemeral=True)
        else:
            await interaction.response.send_message(f"User ID {uid} is not in the list.", ephemeral=True)
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

    log_to_console("VIEWMODS_COMMAND", interaction.user, {"Mod Count": len(authorized_mods)})
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

    log_to_console("VIEWDUTIES_COMMAND", interaction.user, {"Active Duties": len(ACTIVE_DUTIES)})
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="dutystart", description="Start your duty shift and begin receiving reminders")
async def dutystart(interaction: Interaction):
    if not is_authorized_mod(interaction.user.id):
        return await interaction.response.send_message("You are not authorized to start duty.", ephemeral=True)
    
    if interaction.user.id in ACTIVE_DUTIES:
        return await interaction.response.send_message("You are already on duty.", ephemeral=True)

    # Cancel any existing reminder task for this user
    if interaction.user.id in REMINDER_TASKS:
        REMINDER_TASKS[interaction.user.id].cancel()
        del REMINDER_TASKS[interaction.user.id]
        log_to_console("REMINDER_TASK_CANCELLED", interaction.user, {"Reason": "Starting new duty"})

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

    # Start reminder task
    task = asyncio.create_task(schedule_reminder(interaction.user))
    REMINDER_TASKS[interaction.user.id] = task
    log_to_console("REMINDER_TASK_STARTED", interaction.user)

@tree.command(name="endduty", description="End your current duty shift")
async def endduty(interaction: Interaction):
    if interaction.user.id not in ACTIVE_DUTIES:
        return await interaction.response.send_message("You are not on duty.", ephemeral=True)

    await end_duty_session(interaction.user, auto=False)
    await interaction.response.send_message("Duty ended.", ephemeral=True)

@tree.command(name="total", description="View a user's total points")
async def total(interaction: Interaction, user_id: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
    
    try:
        uid = str(int(user_id))
        user_points = points.get(uid, 0)
        log_to_console("TOTAL_COMMAND", interaction.user, {"Queried User ID": uid, "Points": user_points})
        await interaction.response.send_message(f"<@{uid}> has **{user_points}** points.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("Invalid user ID.", ephemeral=True)

@tree.command(name="resetpoints", description="Reset all points (Admin only)")
async def resetpoints(interaction: Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
    
    old_count = len(points)
    points.clear()
    save_points(points)
    
    log_to_console("POINTS_RESET", interaction.user, {"Previous User Count": old_count})
    await interaction.response.send_message("All points have been reset.", ephemeral=True)

# --- Reminder Logic ---
async def schedule_reminder(user):
    """Schedule and send reminders with proper timing"""
    try:
        while user.id in ACTIVE_DUTIES:
            # Wait for 20-30 minutes (1200-1800 seconds)
            sleep_duration = random.randint(180, 300)
            log_to_console("REMINDER_SCHEDULED", user, {
                "Sleep Duration (seconds)": sleep_duration,
                "Sleep Duration (minutes)": round(sleep_duration / 60, 1)
            })
            
            await asyncio.sleep(sleep_duration)
            
            # Check if user is still on duty after sleep
            if user.id not in ACTIVE_DUTIES:
                log_to_console("REMINDER_CANCELLED", user, {"Reason": "User no longer on duty"})
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
                log_to_console("REMINDER_SENT", user, {
                    "Reminder Number": ACTIVE_DUTIES[user.id]['continues'] + 1,
                    "Reminder Time": datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p')
                })
                
                await send_log_embed("Reminder Sent", user, {
                    "User": f"{user} ({user.id})",
                    "Reminder Time": datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'),
                    "Reminder #": ACTIVE_DUTIES[user.id]['continues'] + 1
                })
            except Exception as e:
                log_to_console("REMINDER_FAILED", user, {"Error": str(e)})
                return

            # Wait for response (2 minutes timeout)
            await view.wait()

            # Check response
            if not view.responded:
                log_to_console("REMINDER_NO_RESPONSE", user)
                await end_duty_session(user, auto=True, reason="No response to reminder")
                return
            elif user.id in ACTIVE_DUTIES:
                # Check if 12-hour limit reached
                total_time = datetime.utcnow() - ACTIVE_DUTIES[user.id]['start_time']
                if total_time >= MAX_DUTY_DURATION:
                    log_to_console("DUTY_12_HOUR_LIMIT", user, {"Total Duration": str(total_time)[:-7]})
                    await end_duty_session(user, auto=True, reason="12-hour limit reached")
                    return
                # Continue loop for next reminder
            else:
                # User ended duty
                return
                
    except asyncio.CancelledError:
        log_to_console("REMINDER_TASK_CANCELLED", user)
        return
    except Exception as e:
        log_to_console("REMINDER_ERROR", user, {"Error": str(e)})
        return

async def end_duty_session(user, auto=True, reason="No response"):
    """End a duty session and clean up"""
    if user.id not in ACTIVE_DUTIES:
        return

    # Cancel reminder task
    if user.id in REMINDER_TASKS:
        REMINDER_TASKS[user.id].cancel()
        del REMINDER_TASKS[user.id]
        log_to_console("REMINDER_TASK_CANCELLED", user, {"Reason": "Duty ended"})

    duty = ACTIVE_DUTIES.pop(user.id)
    total_time = datetime.utcnow() - duty['start_time']
    total_minutes = int(total_time.total_seconds() // 60)
    earned_points = total_minutes // 4

    uid = str(user.id)
    points[uid] = points.get(uid, 0) + earned_points
    save_points(points)

    log_to_console("DUTY_ENDED", user, {
        "Auto": auto,
        "Reason": reason if auto else "Manual",
        "Duration (minutes)": total_minutes,
        "Points Earned": earned_points,
        "Total Points": points[uid]
    })

    embed = Embed(
        title="Duty Auto-Ended" if auto else "Duty Ended",
        color=discord.Color.red()
    )
    embed.add_field(name="User", value=f"{user} ({user.id})")
    embed.add_field(name="Start Time", value=duty['start_time'].strftime('%A, %d %B %Y %H:%M %p'))
    embed.add_field(name="End Time", value=datetime.utcnow().strftime('%A, %d %B %Y %H:%M %p'))
    embed.add_field(name="Total Duration", value=f"{total_minutes} minutes")
    embed.add_field(name="Times Continued", value=str(duty['continues']))
    embed.add_field(name="Points Earned", value=str(earned_points))
    if auto:
        embed.add_field(name="Reason", value=reason)

    await send_log_embed(embed=embed)

    # Try to DM the user
    if auto:
    try:
        # Fetch the full user object (ensures we can DM them)
        full_user = await bot.fetch_user(user.id)

        dm = Embed(
            title="Duty Auto-Ended",
            description="Your duty was automatically ended.",
            color=discord.Color.red()
        )
        dm.add_field(name="Reason", value=reason, inline=False)
        dm.add_field(name="Total Duration", value=str(total_time)[:-7])
        dm.add_field(name="Points Earned", value=str(earned_points))

        await full_user.send(embed=dm)
        log_to_console("DM_SENT", full_user)

    except Exception as e:
        log_to_console("DM_FAILED", user, {"Error": str(e)})
        import traceback
        traceback.print_exc()

# --- Bot Events ---
@bot.event
async def on_ready():
    log_to_console("BOT_READY", details={
        "Bot Name": bot.user.name,
        "Bot ID": bot.user.id,
        "Servers": len(bot.guilds)
    })
    
    try:
        synced = await tree.sync()
        log_to_console("COMMANDS_SYNCED", details={"Count": len(synced)})
    except Exception as e:
        log_to_console("SYNC_ERROR", details={"Error": str(e)})
    
    keep_alive()

# --- Run Bot ---
if __name__ == "__main__":
    log_to_console("BOT_STARTING")
    
    # Get Discord token from environment
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        log_to_console("ERROR", details={"Message": "DISCORD_BOT_TOKEN environment variable not found"})
        exit(1)
    
    try:
        bot.run(token)
    except Exception as e:
        log_to_console("BOT_ERROR", details={"Error": str(e)})
