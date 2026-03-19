import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CONFIGS_FILE = "/app/data/server_configs.json"
SEEN_FILE = "/app/data/seen_articles.json"

MIN_HOURS = 1.0
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

COUNTRIES = {
    "🇺🇸 United States": "us",
    "🇬🇧 United Kingdom": "gb",
    "🇦🇺 Australia": "au",
    "🇨🇦 Canada": "ca",
    "🇩🇪 Germany": "de",
    "🇫🇷 France": "fr",
    "🇮🇳 India": "in",
    "🇯🇵 Japan": "jp",
    "🇧🇷 Brazil": "br",
    "🇦🇪 UAE": "ae",
    "🇸🇦 Saudi Arabia": "sa",
    "🇿🇦 South Africa": "za",
}


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


def next_post_time(last_posted: float, interval_hours: float) -> str:
    if last_posted == 0:
        return "very soon"
    next_ts = last_posted + (interval_hours * 3600)
    now = datetime.now(timezone.utc).timestamp()
    diff = next_ts - now
    if diff <= 0:
        return "very soon"
    minutes = int(diff // 60)
    hours = int(diff // 3600)
    if minutes < 60:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    return f"in {hours} hour{'s' if hours != 1 else ''}"


async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=label, value=value)
        for label, value in CATEGORIES.items()
        if current.lower() in label.lower() or current.lower() in value.lower()
    ][:25]


async def country_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=label, value=value)
        for label, value in COUNTRIES.items()
        if current.lower() in label.lower() or current.lower() in value.lower()
    ][:25]


async def interval_autocomplete(interaction: discord.Interaction, current: str):
    presets = [
        ("1 hour  (minimum)",  "1"),
        ("2 hours",            "2"),
        ("6 hours",            "6"),
        ("12 hours",           "12"),
        ("1 day",              "24"),
        ("2 days",             "48"),
        ("1 week",             "168"),
        ("2 weeks",            "336"),
        ("1 month  (maximum)", "730"),
    ]
    results = []
    for label, value in presets:
        if current == "" or current.lower() in label.lower() or current in value:
            results.append(discord.app_commands.Choice(name=label, value=value))
    if current and current.replace(".", "", 1).isdigit():
        results.insert(0, discord.app_commands.Choice(name=f"Custom: {current} hours", value=current))
    return results[:25]


async def limit_autocomplete(interaction: discord.Interaction, current: str):
    return [
        discord.app_commands.Choice(name=f"{i} article{'s' if i > 1 else ''}", value=str(i))
        for i in range(1, 6)
        if current == "" or current in str(i)
    ]


async def fetch_news(api_key: str, category: str, country: str = "us") -> list[dict]:
    url = "https://newsapi.org/v2/top-headlines"
    params = {"apiKey": api_key, "category": category, "country": country, "pageSize": 10}
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
                country = config.get("country", "us")
                interval_hours = float(config.get("interval_hours", 1.0))
                last_posted = float(config.get("last_posted", 0))
                paused = config.get("paused", False)
                limit = int(config.get("limit", 3))

                if paused or not api_key or not channel_id:
                    continue
                if (now.timestamp() - last_posted) / 3600 < interval_hours:
                    continue

                channel = bot.get_channel(int(channel_id))
                if channel is None:
                    continue

                articles = await fetch_news(api_key, category, country)
                guild_seen = set(seen_articles.get(gid, []))
                new_articles = [a for a in articles if a.get("url") and a["url"] not in guild_seen]

                for article in new_articles[:limit]:
                    await channel.send(embed=build_embed(article, category))
                    guild_seen.add(article["url"])

                seen_articles[gid] = list(guild_seen)[-500:]
                save_seen(seen_articles)
                set_config(guild.id, "last_posted", now.timestamp())
                print(f"[Bot] {guild.name} → {len(new_articles[:limit])} article(s)")

            except Exception as e:
                print(f"[Bot] Error in guild {guild.name}: {e}")
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
        "`/news` — Get latest news right now\n"
        "`/categories` — See all news topics\n"
        "`/countries` — See all available countries"
    ), inline=False)
    embed.add_field(name="👑 Owner Only", value=(
        "`/post interval` — Set channel, category, country, limit & frequency\n"
        "`/post now` — Post news immediately\n"
        "`/post stop` — Pause automatic posting\n"
        "`/post resume` — Resume automatic posting\n"
        "`/setup apikey` — Set your NewsAPI key\n"
        "`/setup category` — Change news topic\n"
        "`/setup country` — Change news country\n"
        "`/setup limit` — Set how many articles to post at once\n"
        "`/setup status` — Check all current settings"
    ), inline=False)
    embed.add_field(name="🔑 Need a free NewsAPI key?", value="👉 https://newsapi.org/register", inline=False)
    embed.set_footer(text="Only the server owner can use /setup and /post commands")
    await interaction.response.send_message(embed=embed)


# /categories
@bot.tree.command(name="categories", description="See all available news categories")
async def slash_categories(interaction: discord.Interaction):
    embed = discord.Embed(title="🗂️ Available Categories", description="Use `/post interval` or `/setup category` to pick one:", color=discord.Color.blurple())
    for label, value in CATEGORIES.items():
        embed.add_field(name=label, value=f"`{value}`", inline=True)
    await interaction.response.send_message(embed=embed)


# /countries
@bot.tree.command(name="countries", description="See all available countries for news")
async def slash_countries(interaction: discord.Interaction):
    embed = discord.Embed(title="🌍 Available Countries", description="Use `/post interval` or `/setup country` to pick one:", color=discord.Color.blurple())
    for label, value in COUNTRIES.items():
        embed.add_field(name=label, value=f"`{value}`", inline=True)
    await interaction.response.send_message(embed=embed)


# /news
@bot.tree.command(name="news", description="Get the latest news right now")
@discord.app_commands.autocomplete(category=category_autocomplete)
async def slash_news(interaction: discord.Interaction, category: str = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    api_key = config.get("api_key")
    cat = category or config.get("category", "technology")
    country = config.get("country", "us")
    if not api_key:
        await interaction.response.send_message("❌ No API key set. Owner needs to run `/setup apikey` first.", ephemeral=True)
        return
    await interaction.response.defer()
    articles = await fetch_news(api_key, cat, country)
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


@post_group.command(name="interval", description="Set up automatic news posting — channel, category, country, articles & frequency")
@discord.app_commands.autocomplete(category=category_autocomplete, country=country_autocomplete, hours=interval_autocomplete, limit=limit_autocomplete)
async def post_interval(interaction: discord.Interaction, channel: discord.TextChannel, category: str, hours: str, country: str = "us", limit: str = "3"):
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
        await interaction.response.send_message("❌ Minimum is **1 hour**. NewsAPI doesn't update faster than that.", ephemeral=True)
        return
    if hours_float > MAX_HOURS:
        await interaction.response.send_message("❌ Maximum is **1 month** (730 hours).", ephemeral=True)
        return
    try:
        limit_int = max(1, min(5, int(limit)))
    except ValueError:
        limit_int = 3

    set_config(interaction.guild.id, "channel_id", channel.id)
    set_config(interaction.guild.id, "category", category)
    set_config(interaction.guild.id, "country", country)
    set_config(interaction.guild.id, "interval_hours", hours_float)
    set_config(interaction.guild.id, "limit", limit_int)
    set_config(interaction.guild.id, "last_posted", 0)
    set_config(interaction.guild.id, "paused", False)

    emoji = CATEGORY_EMOJIS[category]
    country_flag = next((k.split()[0] for k, v in COUNTRIES.items() if v == country), "🌍")
    await interaction.response.send_message(
        f"✅ All set!\n"
        f"📢 Channel: {channel.mention}\n"
        f"{emoji} Category: **{category}**\n"
        f"{country_flag} Country: **{country.upper()}**\n"
        f"📰 Articles per post: **{limit_int}**\n"
        f"⏱️ Every **{format_hours(hours_float)}**",
        ephemeral=True
    )


@post_group.command(name="now", description="Post the latest news right now to your configured channel")
async def post_now(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    api_key = config.get("api_key")
    channel_id = config.get("channel_id")
    category = config.get("category", "technology")
    country = config.get("country", "us")
    limit = int(config.get("limit", 3))

    if not api_key:
        await interaction.response.send_message("❌ No API key set. Run `/setup apikey` first.", ephemeral=True)
        return
    if not channel_id:
        await interaction.response.send_message("❌ No channel set. Run `/post interval` first.", ephemeral=True)
        return

    channel = bot.get_channel(int(channel_id))
    if channel is None:
        await interaction.response.send_message("❌ Channel not found. Run `/post interval` to set a new one.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    articles = await fetch_news(api_key, category, country)
    gid = str(interaction.guild.id)
    guild_seen = set(seen_articles.get(gid, []))
    new_articles = [a for a in articles if a.get("url") and a["url"] not in guild_seen]

    sent = 0
    for article in new_articles[:limit]:
        await channel.send(embed=build_embed(article, category))
        guild_seen.add(article["url"])
        sent += 1

    seen_articles[gid] = list(guild_seen)[-500:]
    save_seen(seen_articles)
    set_config(interaction.guild.id, "last_posted", datetime.now(timezone.utc).timestamp())

    if sent == 0:
        await interaction.followup.send("No new articles to post right now — all recent ones have already been posted.", ephemeral=True)
    else:
        await interaction.followup.send(f"✅ Posted **{sent}** new article(s) to {channel.mention}!", ephemeral=True)


@post_group.command(name="stop", description="Stop automatic news posting")
async def post_stop(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    if not config.get("channel_id"):
        await interaction.response.send_message("❌ News posting isn't set up yet. Use `/post interval` first.", ephemeral=True)
        return
    if config.get("paused"):
        await interaction.response.send_message("⏸️ News posting is already stopped.", ephemeral=True)
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
        await interaction.response.send_message("❌ News posting isn't set up yet. Use `/post interval` first.", ephemeral=True)
        return
    if not config.get("paused"):
        await interaction.response.send_message("▶️ News posting is already running.", ephemeral=True)
        return
    set_config(interaction.guild.id, "paused", False)
    set_config(interaction.guild.id, "last_posted", 0)
    await interaction.response.send_message("▶️ News posting **resumed**!", ephemeral=True)


bot.tree.add_command(post_group)


# /setup group
setup_group = discord.app_commands.Group(name="setup", description="Configure the news bot (owner only)")


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


@setup_group.command(name="country", description="Change the news country")
@discord.app_commands.autocomplete(country=country_autocomplete)
async def setup_country(interaction: discord.Interaction, country: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    if country not in COUNTRIES.values():
        await interaction.response.send_message("❌ Invalid country. Use `/countries` to see options.", ephemeral=True)
        return
    set_config(interaction.guild.id, "country", country)
    flag = next((k.split()[0] for k, v in COUNTRIES.items() if v == country), "🌍")
    await interaction.response.send_message(f"{flag} Country updated to **{country.upper()}**!", ephemeral=True)


@setup_group.command(name="limit", description="Set how many articles to post at once (1-5)")
@discord.app_commands.autocomplete(limit=limit_autocomplete)
async def setup_limit(interaction: discord.Interaction, limit: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    try:
        limit_int = max(1, min(5, int(limit)))
    except ValueError:
        await interaction.response.send_message("❌ Please enter a number between 1 and 5.", ephemeral=True)
        return
    set_config(interaction.guild.id, "limit", limit_int)
    await interaction.response.send_message(f"📰 Now posting **{limit_int}** article{'s' if limit_int > 1 else ''} at a time!", ephemeral=True)


@setup_group.command(name="status", description="Check your current bot configuration")
async def setup_status(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return
    config = get_config(interaction.guild.id)
    channel_id = config.get("channel_id")
    category = config.get("category", "Not set")
    country = config.get("country", "us")
    api_key = config.get("api_key")
    interval_hours = float(config.get("interval_hours", 1.0))
    paused = config.get("paused", False)
    last_posted = float(config.get("last_posted", 0))
    limit = int(config.get("limit", 3))
    flag = next((k.split()[0] for k, v in COUNTRIES.items() if v == country), "🌍")

    embed = discord.Embed(title="⚙️ News Bot Status", color=discord.Color.blurple())
    embed.add_field(name="📢 Channel", value=f"<#{channel_id}>" if channel_id else "❌ Not set — use `/post interval`", inline=True)
    embed.add_field(name="🗂️ Category", value=f"{CATEGORY_EMOJIS.get(category, '')} {category}" if category in CATEGORY_EMOJIS else "❌ Not set", inline=True)
    embed.add_field(name="🌍 Country", value=f"{flag} {country.upper()}", inline=True)
    embed.add_field(name="📰 Articles/post", value=str(limit), inline=True)
    embed.add_field(name="⏱️ Interval", value=f"Every {format_hours(interval_hours)}", inline=True)
    embed.add_field(name="📡 Status", value="⏸️ Paused" if paused else f"▶️ Active — next post {next_post_time(last_posted, interval_hours)}", inline=True)
    embed.add_field(name="🔑 API Key", value="✅ Set" if api_key else "❌ Not set — use `/setup apikey`", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(setup_group)


@bot.event
async def on_guild_join(guild: discord.Guild):
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                title="👋 Thanks for adding News Bot!",
                description=(
                    "**Quick setup — just 2 commands:**\n\n"
                    "1️⃣ `/setup apikey YOUR_KEY`\n"
                    "Get a free key at https://newsapi.org/register\n\n"
                    "2️⃣ `/post interval`\n"
                    "Pick your channel, category, country & frequency\n\n"
                    "⚠️ **Important:** Make sure News Bot has **Send Messages** and **Embed Links** permission in the channel you choose!\n\n"
                    "📋 `/help` — all commands\n"
                    "🗂️ `/categories` — news topics\n"
                    "🌍 `/countries` — available countries"
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
    if not post_news.is_running():
        post_news.start()
    print(f"[Bot] Scheduler started!")


bot.run(DISCORD_TOKEN)
