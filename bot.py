import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CONFIGS_FILE = "server_configs.json"
SEEN_FILE = "seen_articles.json"

MIN_HOURS = 5 / 60      # 5 minutes
MAX_HOURS = 24 * 30.4   # ~1 month


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
    return interaction.user.id == interaction.guild.owner_id


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


def format_hours(hours: float) -> str:
    total_minutes = round(hours * 60)
    if total_minutes < 60:
        return f"{total_minutes} minute{'s' if total_minutes != 1 else ''}"
    elif total_minutes < 1440:
        h = total_minutes // 60
        m = total_minutes % 60
        if m == 0:
            return f"{h} hour{'s' if h != 1 else ''}"
        return f"{h}h {m}m"
    else:
        days = total_minutes // 1440
        remaining_hours = (total_minutes % 1440) // 60
        if remaining_hours == 0:
            return f"{days} day{'s' if days != 1 else ''}"
        return f"{days}d {remaining_hours}h"


async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=label, value=value)
        for label, value in CATEGORIES.items()
        if current.lower() in label.lower() or current.lower() in value.lower()
    ]


async def interval_autocomplete(interaction: discord.Interaction, current: str):
    presets = [
        ("⏱️ 5 minutes   (minimum)",  "0.0833"),
        ("⏱️ 15 minutes",             "0.25"),
        ("⏱️ 30 minutes",             "0.5"),
        ("⏱️ 1 hour",                 "1"),
        ("⏱️ 2 hours",                "2"),
        ("⏱️ 6 hours",                "6"),
        ("⏱️ 12 hours",               "12"),
        ("⏱️ 1 day",                  "24"),
        ("⏱️ 2 days",                 "48"),
        ("⏱️ 1 week",                 "168"),
        ("⏱️ 2 weeks",                "336"),
        ("⏱️ 1 month   (maximum)",    "730"),
    ]
    results = []
    for label, value in presets:
        if current == "" or current.lower() in label.lower() or current in value:
            results.append(discord.app_commands.Choice(name=label, value=value))
    # Also allow typing a custom number
    if current and current.replace(".", "", 1).isdigit():
        results.insert(0, discord.app_commands.Choice(name=f"✏️ Custom: {current} hours", value=current))
    return results[:25]


async def fetch_news(api_key: str, category: str) -> list[dict]:
    url = "https://newsapi.org/v2/top-headlines"
    params = {"apiKey": api_key, "category": category, "language": "en", "pageSize": 10}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("articles", [])


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
    now = datetime.now(timezone.utc)
    for guild in bot.guilds:
        gid = str(guild.id)
        config = server_configs.get(gid, {})
        api_key = config.get("api_key")
        channel_id = config.get("channel_id")
        category = config.get("category", "technology")
        interval_hours = float(config.get("interval_hours", 1.0))
        last_posted = config.get("last_posted", 0)
        if not api_key or not channel_id:
            continue
        hours_since = (now.timestamp() - last_posted) / 3600
        if hours_since < interval_hours:
            continue
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            continue
        articles = await fetch_news(api_key, category)
        guild_seen = set(seen_articles.get(gid, []))
        new_count = 0
        for article in articles:
            url = article.get("url")
            if not url or url in guild_seen:
                continue
            guild_seen.add(url)
            embed = build_embed(article, category)
            try:
                await channel.send(embed=embed)
                new_count += 1
            except Exception as e:
                print(f"[Bot] Error: {e}")
        seen_articles[gid] = list(guild_seen)[-500:]
        save_seen(seen_articles)
        set_config(guild.id, "last_posted", now.timestamp())
        print(f"[Bot] {guild.name} → {new_count} article(s)")


@post_news.before_loop
async def before_post():
    await bot.wait_until_ready()


# /help
@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📰 News Bot — Help", description="Here's everything you can do!", color=discord.Color.blurple())
    embed.add_field(name="📋 General", value="`/help` — This menu\n`/news` — Get latest news now\n`/categories` — See all news topics", inline=False)
    embed.add_field(name="⚙️ Setup (Owner Only)", value="`/setup apikey` — Set your NewsAPI key\n`/setup channel` — Set news channel\n`/setup category` — Set news topic\n`/setup status` — Check settings", inline=False)
    embed.add_field(name="📬 Posting (Owner Only)", value="`/post now` — Post news immediately\n`/post interval` — Set auto-post frequency (5 min → 1 month)", inline=False)
    embed.add_field(name="🔑 Need a NewsAPI key?", value="Get one free at https://newsapi.org/register", inline=False)
    embed.set_footer(text="Only server owners can use setup & post commands")
    await interaction.response.send_message(embed=embed)


# /categories
@bot.tree.command(name="categories", description="See all available news categories")
async def slash_categories(interaction: discord.Interaction):
    embed = discord.Embed(title="🗂️ Available News Categories", description="Use `/setup category` or `/post now` to pick one:", color=discord.Color.blurple())
    for label, value in CATEGORIES.items():
        embed.add_field(name=label, value=f"`{value}`", inline=True)
    await interaction.response.send_message(embed=embed)


# /news
@bot.tree.command(name="news", description="Get the latest news right now")
@discord.app_commands.autocomplete(category=category_autocomplete)
async def slash_news(interaction: discord.Interaction, category: str = None):
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


# /post group
post_group = discord.app_commands.Group(name="post", description="Posting controls (owner only)")


@post_group.command(name="now", description="Post news to a channel right now")
@discord.app_commands.autocomplete(category=category_autocomplete)
async def post_now(interaction: discord.Interaction, category: str = None, channel: discord.TextChannel = None):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    api_key = config.get("api_key")
    cat = category or config.get("category", "technology")
    ch = channel or (bot.get_channel(int(config["channel_id"])) if config.get("channel_id") else None)
    if not api_key:
        await interaction.response.send_message("❌ No API key set. Run `/setup apikey` first.", ephemeral=True)
        return
    if not ch:
        await interaction.response.send_message("❌ No channel set. Run `/setup channel` or specify one here.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    articles = await fetch_news(api_key, cat)
    gid = str(interaction.guild.id)
    guild_seen = set(seen_articles.get(gid, []))
    sent = 0
    for article in articles:
        url = article.get("url")
        if not url or url in guild_seen:
            continue
        guild_seen.add(url)
        await ch.send(embed=build_embed(article, cat))
        sent += 1
    seen_articles[gid] = list(guild_seen)[-500:]
    save_seen(seen_articles)
    await interaction.followup.send(f"✅ Posted **{sent}** new article(s) to {ch.mention}!", ephemeral=True)


@post_group.command(name="interval", description="Set how often news is posted — pick an option or type custom hours (e.g. 3.5)")
@discord.app_commands.autocomplete(hours=interval_autocomplete)
async def post_interval(interaction: discord.Interaction, hours: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    try:
        hours_float = float(hours)
    except ValueError:
        await interaction.response.send_message("❌ Please enter a valid number or pick from the dropdown.", ephemeral=True)
        return
    if hours_float < MIN_HOURS:
        await interaction.response.send_message("❌ Minimum is **5 minutes**. Try `0.0833` or pick from the dropdown.", ephemeral=True)
        return
    if hours_float > MAX_HOURS:
        await interaction.response.send_message("❌ Maximum is **1 month** (730 hours).", ephemeral=True)
        return
    set_config(interaction.guild.id, "interval_hours", hours_float)
    await interaction.response.send_message(f"⏱️ News will now be posted every **{format_hours(hours_float)}**!", ephemeral=True)


bot.tree.add_command(post_group)


# /setup group
setup_group = discord.app_commands.Group(name="setup", description="Configure the news bot (owner only)")


@setup_group.command(name="channel", description="Set which channel news will be posted in")
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    set_config(interaction.guild.id, "channel_id", channel.id)
    await interaction.response.send_message(f"✅ News will now be posted in {channel.mention}!", ephemeral=True)


@setup_group.command(name="category", description="Set the news category")
@discord.app_commands.autocomplete(category=category_autocomplete)
async def setup_category(interaction: discord.Interaction, category: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    if category not in VALID_CATEGORIES:
        await interaction.response.send_message("❌ Invalid category. Use `/categories` to see all options.", ephemeral=True)
        return
    set_config(interaction.guild.id, "category", category)
    await interaction.response.send_message(f"{CATEGORY_EMOJIS[category]} Category set to **{category}**!", ephemeral=True)


@setup_group.command(name="apikey", description="Set your NewsAPI key (free at newsapi.org)")
async def setup_apikey(interaction: discord.Interaction, apikey: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    articles = await fetch_news(apikey, "general")
    if not articles:
        await interaction.followup.send("❌ That API key doesn't work. Get a free one at https://newsapi.org/register", ephemeral=True)
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
    embed = discord.Embed(title="⚙️ News Bot Configuration", color=discord.Color.blurple())
    embed.add_field(name="📢 Channel", value=f"<#{channel_id}>" if channel_id else "❌ Not set — use `/setup channel`", inline=False)
    embed.add_field(name="🗂️ Category", value=f"{CATEGORY_EMOJIS.get(category, '')} {category}" if category in CATEGORY_EMOJIS else "❌ Not set", inline=False)
    embed.add_field(name="🔑 API Key", value="✅ Set" if api_key else "❌ Not set — use `/setup apikey`", inline=False)
    embed.add_field(name="⏱️ Interval", value=f"Every {format_hours(interval_hours)}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(setup_group)


@bot.event
async def on_guild_join(guild: discord.Guild):
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                title="👋 Thanks for adding News Bot!",
                description=(
                    "To get started, the **server owner** needs to run:\n\n"
                    "1️⃣ `/setup apikey YOUR_KEY` — free key at https://newsapi.org/register\n"
                    "2️⃣ `/setup channel #your-channel` — where news gets posted\n"
                    "3️⃣ `/setup category` — pick your news topic\n\n"
                    "📋 Run `/help` to see all commands!\n"
                    "🗂️ Run `/categories` to see all news topics!"
                ),
                color=discord.Color.blurple(),
            )
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
    post_news.start()
    print(f"[Bot] Scheduler started!")


bot.run(DISCORD_TOKEN)
