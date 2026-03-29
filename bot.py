import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CONFIGS_FILE = "/app/data/server_configs.json"
SEEN_FILE = "/app/data/seen_articles.json"

MIN_HOURS = 0.0833
MAX_HOURS = 730.0

os.makedirs("/app/data", exist_ok=True)


def load_configs() -> dict:
    if os.path.exists(CONFIGS_FILE):
        with open(CONFIGS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_configs(configs: dict):
    with open(CONFIGS_FILE, "w") as f:
        json.dump(configs, f, indent=2)

def load_seen() -> dict:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}

def save_seen(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)


server_configs: dict = load_configs()
seen_articles: dict = load_seen()


def get_config(guild_id: int) -> dict:
    return server_configs.get(str(guild_id), {})

def set_config(guild_id: int, key: str, value):
    gid = str(guild_id)
    if gid not in server_configs:
        server_configs[gid] = {}
    server_configs[gid][key] = value
    save_configs(server_configs)

def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.user.id == interaction.guild.owner_id


CATEGORIES = {
    "💼 Business":      "business",
    "🎬 Entertainment": "entertainment",
    "🌐 General":       "general",
    "❤️ Health":        "health",
    "🔬 Science":       "science",
    "⚽ Sports":        "sports",
    "💻 Technology":    "technology",
}

CATEGORY_COLORS = {
    "business":      discord.Color.gold(),
    "entertainment": discord.Color.purple(),
    "general":       discord.Color.blurple(),
    "health":        discord.Color.green(),
    "science":       discord.Color.teal(),
    "sports":        discord.Color.orange(),
    "technology":    discord.Color.blue(),
}

CATEGORY_EMOJIS = {
    "business":      "💼",
    "entertainment": "🎬",
    "general":       "🌐",
    "health":        "❤️",
    "science":       "🔬",
    "sports":        "⚽",
    "technology":    "💻",
}

VALID_CATEGORIES = list(CATEGORY_COLORS.keys())

SUPPORT_USER_ID = 1400777871253045350


def format_hours(hours: float) -> str:
    total_minutes = round(hours * 60)
    if total_minutes < 60:
        return f"{total_minutes} minute{'s' if total_minutes != 1 else ''}"
    elif total_minutes < 1440:
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h}h {m}m" if m else f"{h} hour{'s' if h != 1 else ''}"
    else:
        days = total_minutes // 1440
        rh = (total_minutes % 1440) // 60
        return f"{days}d {rh}h" if rh else f"{days} day{'s' if days != 1 else ''}"


async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=label, value=value)
        for label, value in CATEGORIES.items()
        if current.lower() in label.lower() or current.lower() in value.lower()
    ][:25]


async def interval_autocomplete(interaction: discord.Interaction, current: str):
    presets = [
        ("5 minutes (minimum)", "0.0833"),
        ("15 minutes",          "0.25"),
        ("30 minutes",          "0.5"),
        ("1 hour",              "1"),
        ("2 hours",             "2"),
        ("6 hours",             "6"),
        ("12 hours",            "12"),
        ("1 day",               "24"),
        ("2 days",              "48"),
        ("1 week",              "168"),
        ("2 weeks",             "336"),
        ("1 month (maximum)",   "730"),
    ]
    results = []
    for label, value in presets:
        if current == "" or current.lower() in label.lower() or current in value:
            results.append(discord.app_commands.Choice(name=label, value=value))
    if current and current.replace(".", "", 1).isdigit():
        results.insert(0, discord.app_commands.Choice(name=f"Custom: {current} hours", value=current))
    return results[:25]


async def fetch_news(api_key: str, category: str) -> list[dict]:
    url = "https://newsapi.org/v2/top-headlines"
    params = {"apiKey": api_key, "category": category, "language": "en", "pageSize": 10}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("articles", [])
    except Exception as e:
        print(f"[NewsAPI] fetch error: {e}")
        return []


def build_embed(article: dict, category: str) -> discord.Embed:
    color = CATEGORY_COLORS.get(category, discord.Color.default())
    emoji = CATEGORY_EMOJIS.get(category, "📰")
    title = article.get("title") or "No title"
    description = article.get("description") or ""
    url = article.get("url") or ""
    image_url = article.get("urlToImage")
    source = article.get("source", {}).get("name", "Unknown source")
    embed = discord.Embed(title=f"{emoji}  {title}", description=description, url=url, color=color, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"{source}  •  {category.capitalize()} news")
    if image_url:
        embed.set_image(url=image_url)
    return embed


intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)


@tasks.loop(minutes=1)
async def post_news():
    try:
        now = datetime.now(timezone.utc)
        for guild in bot.guilds:
            try:
                gid = str(guild.id)
                config = server_configs.get(gid, {})
                api_key = config.get("api_key")
                channel_id = config.get("channel_id")
                category = config.get("category", "technology")
                interval_hours = float(config.get("interval_hours", 1.0))
                last_posted = float(config.get("last_posted", 0))
                paused = config.get("paused", False)
                if paused or not api_key or not channel_id:
                    continue
                if (now.timestamp() - last_posted) / 3600 < interval_hours:
                    continue
                channel = bot.get_channel(int(channel_id))
                if channel is None:
                    continue
                articles = await fetch_news(api_key, category)
                guild_seen = set(seen_articles.get(gid, []))
                new_articles = [a for a in articles if a.get("url") and a["url"] not in guild_seen]
                sent = 0
                for article in new_articles[:3]:
                    await channel.send(embed=build_embed(article, category))
                    guild_seen.add(article["url"])
                    sent += 1
                    if sent < len(new_articles[:3]):
                        await asyncio.sleep(1.5)  # rate limit: space out messages
                seen_articles[gid] = list(guild_seen)[-500:]
                save_seen(seen_articles)
                set_config(guild.id, "last_posted", now.timestamp())
                print(f"[Bot] {guild.name} → {sent} article(s)")
            except Exception as e:
                print(f"[Bot] Error in guild {guild.name}: {e}")
            # rate limit: pause briefly between guilds
            await asyncio.sleep(1)
    except Exception as e:
        print(f"[Bot] Loop error: {e}")


@post_news.error
async def post_news_error(error):
    print(f"[Bot] Task error: {error}")
    if not post_news.is_running():
        post_news.start()


@post_news.before_loop
async def before_post():
    await bot.wait_until_ready()


# /help
@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📰 News Bot — Commands", color=discord.Color.blurple())
    embed.add_field(name="👤 Everyone", value=(
        "`/help` — This menu\n"
        "`/news` — Get latest news now\n"
        "`/categories` — See all news topics\n"
        "`/support` — Get help\n"
        "`/report` — Report a bug"
    ), inline=False)
    embed.add_field(name="👑 Owner Only", value=(
        "`/setup apikey` — Set your NewsAPI key\n"
        "`/setup category` — Change news topic\n"
        "`/setup status` — Check current settings\n"
        "`/post interval` — Set channel, category & frequency\n"
        "`/post stop` — Pause automatic posting\n"
        "`/post resume` — Resume automatic posting"
    ), inline=False)
    embed.add_field(name="🔑 Need a free NewsAPI key?", value="👉 https://newsapi.org/register", inline=False)
    embed.set_footer(text="Only the server owner can use /setup and /post commands")
    await interaction.response.send_message(embed=embed)


# /categories
@bot.tree.command(name="categories", description="See all available news categories")
async def slash_categories(interaction: discord.Interaction):
    embed = discord.Embed(title="🗂️ Available News Categories", description="Pick one with `/post interval` or `/setup category`:", color=discord.Color.blurple())
    for label, value in CATEGORIES.items():
        embed.add_field(name=label, value=f"`{value}`", inline=True)
    await interaction.response.send_message(embed=embed)


# /news — with cooldown: 1 use per 30 seconds per user
@bot.tree.command(name="news", description="Get the latest news right now")
@discord.app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
@discord.app_commands.autocomplete(category=category_autocomplete)
async def slash_news(interaction: discord.Interaction, category: str = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    api_key = config.get("api_key")
    cat = category or config.get("category", "technology")
    if not api_key:
        await interaction.response.send_message("❌ No API key set. Owner needs to run `/setup apikey` first.", ephemeral=True)
        return
    await interaction.response.defer()
    articles = await fetch_news(api_key, cat)
    if not articles:
        await interaction.followup.send("No articles found right now. Try again later.")
        return
    sent = 0
    for article in articles:
        if sent >= 3:
            break
        await interaction.followup.send(embed=build_embed(article, cat))
        sent += 1
        if sent < 3:
            await asyncio.sleep(1.5)  # rate limit: space out messages


@slash_news.error
async def slash_news_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        retry = round(error.retry_after)
        await interaction.response.send_message(
            f"⏳ Slow down! You can use `/news` again in **{retry}s**.",
            ephemeral=True
        )


# /support
@bot.tree.command(name="support", description="Get help with the bot")
async def slash_support(interaction: discord.Interaction):
    embed = discord.Embed(title="🆘 Need Help?", description="Here's how to get support for News Bot:", color=discord.Color.blurple())
    embed.add_field(name="📋 Check these first", value=(
        "• Run `/help` to see all commands\n"
        "• Run `/setup status` to check your config\n"
        "• Make sure the bot has **Send Messages** & **Embed Links** permissions\n"
        "• Make sure your NewsAPI key is valid (free at https://newsapi.org/register)"
    ), inline=False)
    embed.add_field(name="🐛 Found a bug?", value="Use `/report your issue here` to send it to the developer!", inline=False)
    embed.add_field(name="👑 Developer", value=f"<@{SUPPORT_USER_ID}>", inline=False)
    await interaction.response.send_message(embed=embed)


# /report
@bot.tree.command(name="report", description="Report a bug or issue with the bot")
async def slash_report(interaction: discord.Interaction, issue: str):
    await interaction.response.defer(ephemeral=True)
    try:
        user = await bot.fetch_user(SUPPORT_USER_ID)
        embed = discord.Embed(title="🐛 Bug Report", description=issue, color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="👤 Reported by", value=f"{interaction.user} (ID: {interaction.user.id})", inline=False)
        embed.add_field(name="🏠 Server", value=f"{interaction.guild.name} (ID: {interaction.guild.id})", inline=False)
        await user.send(embed=embed)
        await interaction.followup.send("✅ Report sent to the developer! Thank you.", ephemeral=True)
    except Exception as e:
        print(f"[Bot] Failed to send report: {e}")
        await interaction.followup.send("❌ Failed to send report. Please try again later.", ephemeral=True)


# /post group
post_group = discord.app_commands.Group(name="post", description="Posting controls (owner only)")


@post_group.command(name="interval", description="Set the channel, category and how often news is posted")
@discord.app_commands.autocomplete(category=category_autocomplete, hours=interval_autocomplete)
async def post_interval(interaction: discord.Interaction, channel: discord.TextChannel, category: str, hours: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    if category not in VALID_CATEGORIES:
        await interaction.response.send_message("❌ Invalid category. Use `/categories` to see options.", ephemeral=True)
        return
    try:
        hours_float = round(float(hours), 4)
    except ValueError:
        await interaction.response.send_message("❌ Invalid time. Pick from the dropdown or type a number.", ephemeral=True)
        return
    if hours_float < MIN_HOURS - 0.001:
        await interaction.response.send_message("❌ Minimum is **5 minutes** (0.0833).", ephemeral=True)
        return
    if hours_float > MAX_HOURS:
        await interaction.response.send_message("❌ Maximum is **1 month** (730 hours).", ephemeral=True)
        return
    set_config(interaction.guild.id, "channel_id", channel.id)
    set_config(interaction.guild.id, "category", category)
    set_config(interaction.guild.id, "interval_hours", hours_float)
    set_config(interaction.guild.id, "last_posted", 0)
    set_config(interaction.guild.id, "paused", False)
    emoji = CATEGORY_EMOJIS[category]
    await interaction.response.send_message(
        f"✅ All set!\n📢 Channel: {channel.mention}\n{emoji} Category: **{category}**\n⏱️ Every **{format_hours(hours_float)}**",
        ephemeral=True
    )


@post_group.command(name="stop", description="Stop automatic news posting")
async def post_stop(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    if not config.get("channel_id"):
        await interaction.response.send_message("❌ Not set up yet. Use `/post interval` first.", ephemeral=True)
        return
    if config.get("paused"):
        await interaction.response.send_message("⏸️ Already stopped.", ephemeral=True)
        return
    set_config(interaction.guild.id, "paused", True)
    await interaction.response.send_message("⏹️ News posting **stopped**. Use `/post resume` to start again.", ephemeral=True)


@post_group.command(name="resume", description="Resume automatic news posting")
async def post_resume(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    if not config.get("channel_id"):
        await interaction.response.send_message("❌ Not set up yet. Use `/post interval` first.", ephemeral=True)
        return
    if not config.get("paused"):
        await interaction.response.send_message("▶️ Already running.", ephemeral=True)
        return
    set_config(interaction.guild.id, "paused", False)
    set_config(interaction.guild.id, "last_posted", 0)
    await interaction.response.send_message("▶️ News posting **resumed**!", ephemeral=True)


bot.tree.add_command(post_group)


# /setup group
setup_group = discord.app_commands.Group(name="setup", description="Configure the news bot (owner only)")


@setup_group.command(name="category", description="Change the news category")
@discord.app_commands.autocomplete(category=category_autocomplete)
async def setup_category(interaction: discord.Interaction, category: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    if category not in VALID_CATEGORIES:
        await interaction.response.send_message("❌ Invalid category. Use `/categories` to see options.", ephemeral=True)
        return
    set_config(interaction.guild.id, "category", category)
    await interaction.response.send_message(f"{CATEGORY_EMOJIS[category]} Category updated to **{category}**!", ephemeral=True)


@setup_group.command(name="apikey", description="Set your NewsAPI key (free at newsapi.org)")
async def setup_apikey(interaction: discord.Interaction, apikey: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    articles = await fetch_news(apikey, "general")
    if not articles:
        await interaction.followup.send("❌ That API key doesn't work. Get one free at https://newsapi.org/register", ephemeral=True)
        return
    set_config(interaction.guild.id, "api_key", apikey)
    await interaction.followup.send("✅ API key saved and verified!", ephemeral=True)


@setup_group.command(name="status", description="Check your current bot configuration")
async def setup_status(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    channel_id = config.get("channel_id")
    category = config.get("category", "Not set")
    api_key = config.get("api_key")
    interval_hours = float(config.get("interval_hours", 1.0))
    paused = config.get("paused", False)
    embed = discord.Embed(title="⚙️ News Bot Status", color=discord.Color.blurple())
    embed.add_field(name="📢 Channel", value=f"<#{channel_id}>" if channel_id else "❌ Not set — use `/post interval`", inline=False)
    embed.add_field(name="🗂️ Category", value=f"{CATEGORY_EMOJIS.get(category, '')} {category}" if category in CATEGORY_EMOJIS else "❌ Not set", inline=False)
    embed.add_field(name="🔑 API Key", value="✅ Set" if api_key else "❌ Not set — use `/setup apikey`", inline=False)
    embed.add_field(name="⏱️ Interval", value=f"Every {format_hours(interval_hours)}", inline=False)
    embed.add_field(name="📡 Status", value="⏸️ Paused — use `/post resume`" if paused else "▶️ Active", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(setup_group)


@bot.event
async def on_guild_join(guild: discord.Guild):
    embed = discord.Embed(
        title="👋 Thanks for adding News Bot!",
        description=f"Hey! Thanks for adding **News Bot** to **{guild.name}**. Here's how to get set up in 2 minutes:",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Step 1 — Get a free NewsAPI key",
        value=(
            "1. Go to 👉 https://newsapi.org/register\n"
            "2. Sign up for free\n"
            "3. Copy your API key\n"
            "4. Run this command in your server:\n"
            "```/setup apikey YOUR_KEY_HERE```"
        ),
        inline=False,
    )
    embed.add_field(
        name="Step 2 — Set up your news channel",
        value=(
            "Run this command in your server:\n"
            "```/post interval```\n"
            "Then pick your **channel**, **category** and **how often** to post!"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Important",
        value="Make sure News Bot has **Send Messages** and **Embed Links** permissions in the channel you choose!",
        inline=False,
    )
    embed.add_field(
        name="📋 Useful commands",
        value=(
            "`/help` — see all commands\n"
            "`/categories` — see all news topics\n"
            "`/setup status` — check your settings\n"
            "`/support` — get help\n"
            "`/report` — report a bug"
        ),
        inline=False,
    )
    embed.set_footer(text="Need help? Run /support in your server!")

    try:
        owner = guild.owner
        if owner:
            await owner.send(embed=embed)
            print(f"[Bot] Sent setup DM to owner of {guild.name}")
            return
    except Exception as e:
        print(f"[Bot] Could not DM owner of {guild.name}: {e}")

    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            await channel.send(embed=embed)
            break


@bot.event
async def on_ready():
    print(f"[Bot] Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"[Bot] Active in {len(bot.guilds)} server(s)")
    try:
        synced = await bot.tree.sync()
        print(f"[Bot] Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"[Bot] Sync error: {e}")
    if not post_news.is_running():
        post_news.start()
    print(f"[Bot] Scheduler started!")


bot.run(DISCORD_TOKEN)
