import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import json
from telegram.constants import ParseMode
from html import escape
import os
from datetime import datetime, timedelta, time
import difflib
import re
import asyncio
import random
import statistics
import csv
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.realpath(__file__))
env_path = os.path.join(script_dir, '.env')
load_dotenv(env_path)

# --- CONFIGURATION ---
FOOD_API_URL = "https://api.dentistry.foodlg.com/apif/dishcreate"
DEFAULT_GOAL = 50.0 
API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- DATABASE SETUP ---
DATA_FILE = os.path.join(script_dir, 'user_data.json')

# --- TRAINING DATA SETUP ---
TRAINING_DIR = os.path.join(script_dir, 'training_data')
if not os.path.exists(TRAINING_DIR):
    os.makedirs(TRAINING_DIR)
    
TRAINING_LOG_FILE = os.path.join(TRAINING_DIR, 'feedback_log.csv')

if not os.path.exists(TRAINING_LOG_FILE):
    with open(TRAINING_LOG_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "image_path", "user_provided_name"])

files_config = {
    'foods': 'foodtypes.json',                  
    'f_to_g': 'foodIDtoAdviceGroup.json',         
    'g_to_info': 'sugarAdviceExtraInfo.json',     
    'g_to_sugar': 'sugarAdvice.json',             
    'lang': 'zh.json'                             
}

STATIC_DB = {}

def load_static_databases():
    """Loads all JSONs and builds the 3-step hierarchy maps."""
    for key, filename in files_config.items():
        path = os.path.join(script_dir, filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                STATIC_DB[key] = data
                print(f"✅ Loaded {key} ({len(data)} records)")
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
            STATIC_DB[key] = {} if key != 'foods' else []

    STATIC_DB['map_f_to_g'] = {}
    raw_f_to_g = STATIC_DB.get('f_to_g', {})
    if isinstance(raw_f_to_g, dict):
        for fid, gid in raw_f_to_g.items():
            STATIC_DB['map_f_to_g'][str(fid)] = str(gid)

    STATIC_DB['map_group_info'] = {}
    raw_g_to_info = STATIC_DB.get('g_to_info', [])
    if isinstance(raw_g_to_info, list):
        for inner_list in raw_g_to_info:
            if isinstance(inner_list, list):
                for obj in inner_list:
                    gid = str(obj.get('group')) 
                    code = obj.get('messageCode')
                    if gid and code:
                        if gid not in STATIC_DB['map_group_info']:
                            STATIC_DB['map_group_info'][gid] = []
                        STATIC_DB['map_group_info'][gid].append(code)

    STATIC_DB['map_sugar_advice'] = {}
    raw_g_to_sugar = STATIC_DB.get('g_to_sugar', [])
    if isinstance(raw_g_to_sugar, list):
        for inner_list in raw_g_to_sugar:
            if isinstance(inner_list, list):
                for obj in inner_list:
                    gid = str(obj.get('groupId')) 
                    code = obj.get('messageCode')
                    if gid and code:
                        if gid not in STATIC_DB['map_sugar_advice']:
                            STATIC_DB['map_sugar_advice'][gid] = []
                        STATIC_DB['map_sugar_advice'][gid].append(code)

    STATIC_DB['map_lang'] = STATIC_DB.get('lang', {})

def load_data():
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_data(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

# Initialize
load_static_databases()
food_types = STATIC_DB.get('foods', [])

# --- HELPER FUNCTIONS ---

def schedule_user_jobs(context: ContextTypes.DEFAULT_TYPE, uid: str, user_data: dict):
    """Schedules custom time jobs for a specific user, replacing old ones."""
    chat_id = int(uid)
    
    # 1. Morning Bump
    morn_job_name = f"morn_{uid}"
    current_morn_jobs = context.job_queue.get_jobs_by_name(morn_job_name)
    for job in current_morn_jobs: job.schedule_removal()
        
    morn_time_str = user_data.get('morning_time', '09:00')
    if morn_time_str != "OFF":
        try:
            t = datetime.strptime(morn_time_str, "%H:%M").time()
            context.job_queue.run_daily(daily_morning_check, time=t, chat_id=chat_id, name=morn_job_name)
        except: pass

    # 2. Weekly Report
    week_job_name = f"week_{uid}"
    current_week_jobs = context.job_queue.get_jobs_by_name(week_job_name)
    for job in current_week_jobs: job.schedule_removal()
        
    week_time_str = user_data.get('weekly_time', 'Sun 21:00')
    if week_time_str != "OFF":
        try:
            day_str, t_str = week_time_str.split(" ")
            days = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
            day_idx = days.get(day_str.capitalize(), 6)
            t = datetime.strptime(t_str, "%H:%M").time()
            context.job_queue.run_daily(weekly_custom_job, time=t, days=(day_idx,), chat_id=chat_id, name=week_job_name)
        except: pass

def get_group_info_for_food(food_id):
    food_id = str(food_id)
    gid = STATIC_DB['map_f_to_g'].get(food_id)
    if not gid: return None
    codes = STATIC_DB['map_group_info'].get(gid, [])
    if not codes: return None
    code = random.choice(codes)
    return STATIC_DB['map_lang'].get(code)

def get_sugar_advice_for_food(food_id):
    food_id = str(food_id)
    gid = STATIC_DB['map_f_to_g'].get(food_id)
    if not gid: return None
    codes = STATIC_DB['map_sugar_advice'].get(gid, [])
    if not codes: return None
    code = random.choice(codes)
    return STATIC_DB['map_lang'].get(code)

def get_random_sugar_advice():
    valid_codes = [code for codes in STATIC_DB['map_sugar_advice'].values() for code in codes]
    if valid_codes:
        return STATIC_DB['map_lang'].get(random.choice(valid_codes), "Drink water to flush out excess sugar!")
    return "Drink water to flush out excess sugar!"

def get_random_group_info():
    valid_codes = [code for codes in STATIC_DB['map_group_info'].values() for code in codes]
    if valid_codes:
        return STATIC_DB['map_lang'].get(random.choice(valid_codes), "Regular dental checkups help catch decay early!")
    return "Regular dental checkups help catch decay early!"

def get_daily_tip():
    lang_map = STATIC_DB.get('map_lang', {})
    daily_tip_keys = [key for key in lang_map.keys() if key.startswith('DAILY_TIP_')]
    if not daily_tip_keys: return "Drink water to flush out excess sugar!" 
    random_key = random.choice(daily_tip_keys)
    return lang_map.get(random_key)

def parse_ai_response(response):
    predictions = []
    try:
        data = response.json()
        if 'predict' in data:
            raw = data['predict']
            if isinstance(raw, str):
                for line in raw.split('\n'):
                    if ':' in line: predictions.append(line.split(':')[0].strip())
                    elif line.strip(): predictions.append(line.strip())
            elif isinstance(raw, list): predictions = raw
    except: pass
    return list(set(predictions))

def search_food_database(query, limit=5):
    query_lower = query.lower().strip()
    query_tokens = set(re.split(r'[^a-z0-9]+', query_lower))
    query_tokens.discard('') 
    scored = []
    for food in food_types:
        name_lower = food['name'].lower()
        if query_lower in name_lower:
            scored.append((2.0 - len(food['name'])/1000, food))
            continue
        name_tokens = set(re.split(r'[^a-z0-9]+', name_lower))
        if query_tokens.intersection(name_tokens):
            score = 1.0 + len(query_tokens.intersection(name_tokens)) / len(query_tokens.union(name_tokens))
            scored.append((score, food))
            continue
        sim = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
        if sim > 0.4: scored.append((sim, food))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:limit]]

def get_daily_sums(user_history, target_date_str):
    sums = {'sugar': 0.0, 'cal': 0.0, 'prot': 0.0, 'fat': 0.0}
    for entry in user_history:
        if entry['date'].startswith(target_date_str):
            sums['sugar'] += float(entry.get('sugar', 0))
            try: sums['cal'] += float(entry.get('calories', 0))
            except: pass
            try: sums['prot'] += float(entry.get('protein', 0))
            except: pass
            try: sums['fat'] += float(entry.get('fat', 0))
            except: pass
    return sums

def generate_progress_bar(current, total):
    if total == 0: total = 1 
    percent = min(current / total, 1.0)
    filled_blocks = int(percent * 10)
    empty_blocks = 10 - filled_blocks
    
    if current > total: bar = "🟥" * 10 
    elif current > (total * 0.8): bar = "🟨" * filled_blocks 
    else: bar = "🟩" * filled_blocks 
        
    return f"{bar}{'⬜' * empty_blocks}", int(percent*100)

def create_past_days_keyboard():
    keyboard = []
    today = datetime.now()
    for i in range(7):
        d = today - timedelta(days=i)
        if i == 0: label = "Today"
        elif i == 1: label = "Yesterday"
        else: label = d.strftime("%a, %d %b")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"late_date:{i}")])
    return InlineKeyboardMarkup(keyboard)

def get_streak_display(streak):
    """Generates the simple text streak display."""
    if streak == 0:
        return "🔥 Streak: 0 days!"
    elif streak == 1:
        return "🔥 Streak: 1 day!"
    else:
        return f"🔥 Streak: {streak} days!"

def get_settings_keyboard(user_settings):
    """Generates the interactive settings menu."""
    morn_val = user_settings.get('morning_time', '09:00')
    week_val = user_settings.get('weekly_time', 'Sun 21:00')
    meal_state = "✅ ON" if user_settings.get('reminders_mealtime', True) else "❌ OFF"

    keyboard = [
        [InlineKeyboardButton(f"Morning Bump: {morn_val}", callback_data="edit_morn")],
        [InlineKeyboardButton(f"Weekly Report: {week_val}", callback_data="edit_week")],
        [InlineKeyboardButton(f"Mealtime Nudges: {meal_state}", callback_data="toggle_meal")]
    ]
    return InlineKeyboardMarkup(keyboard)
    
def get_weekly_analysis(user_history, sugar_goal):
    today = datetime.now()
    seven_days_ago = today - timedelta(days=7)
    daily_sugar = {}
    weekly_items = []
    
    for entry in user_history:
        try:
            entry_date = datetime.strptime(entry['date'], "%Y-%m-%d %H:%M")
            if entry_date > seven_days_ago:
                day_name = entry_date.strftime("%a")
                sug = float(entry.get('sugar', 0))
                daily_sugar[day_name] = daily_sugar.get(day_name, 0) + sug
                weekly_items.append(entry) 
        except: continue
            
    total_sugar = sum(daily_sugar.values())
    days_logged = len(daily_sugar) if len(daily_sugar) > 0 else 1
    avg_sugar = total_sugar / days_logged
    
    graph_text = ""
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        d_name = d.strftime("%a")
        amount = daily_sugar.get(d_name, 0)
        
        if amount == 0: 
            bar = "⬜"
        else:
            blocks = max(1, int(amount // 10)) 
            percent = amount / sugar_goal
            if percent <= 0.8: bar = "🟩" * blocks
            elif percent <= 1.0: bar = "🟨" * blocks
            else: bar = "🟥" * blocks
                
        graph_text += f"<code>{d_name}:</code> {bar} ({int(amount)}g)\n"

    weekly_items.sort(key=lambda x: float(x.get('sugar', 0)), reverse=True)
    top_3_overall = weekly_items[:3]
    
    return avg_sugar, graph_text, top_3_overall

def predict_user_habits(history):
    if not history: return []
    cutoff = datetime.now() - timedelta(days=14)
    timestamps = []
    for h in history:
        try:
            dt = datetime.strptime(h['date'], "%Y-%m-%d %H:%M")
            if dt > cutoff: timestamps.append(dt)
        except: pass
        
    if not timestamps: return []
    
    morning_hours = [t.hour for t in timestamps if 5 <= t.hour < 11]
    midday_hours = [t.hour for t in timestamps if 11 <= t.hour < 16]
    evening_hours = [t.hour for t in timestamps if 16 <= t.hour < 23]
    
    habits = []
    if morning_hours: habits.append(statistics.mode(morning_hours))
    if midday_hours: habits.append(statistics.mode(midday_hours))
    if evening_hours: habits.append(statistics.mode(evening_hours))
    
    return list(set(habits))

async def show_edit_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    all_data = load_data()
    uid = str(update.effective_user.id)
    history = all_data.get(uid, {}).get('history', [])
    
    query = update.callback_query
    
    if not history:
        text = "❌ Your history is empty."
        if query: await query.edit_message_text(text)
        else: await update.message.reply_text(text)
        return
        
    items_per_page = 10
    indexed_history = list(enumerate(history))[::-1]
    total_pages = max(1, (len(indexed_history) - 1) // items_per_page + 1)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_items = indexed_history[start_idx:end_idx]
    
    msg = f"📝 <b>Edit Entries (Page {page+1}/{total_pages})</b>\n\n"
    keyboard = []
    row = []
    
    for i, (orig_idx, item) in enumerate(page_items):
        display_num = start_idx + i + 1
        try:
            dt = datetime.strptime(item['date'], "%Y-%m-%d %H:%M")
            formatted_date = dt.strftime("%d %b, %H:%M")
        except:
            formatted_date = item['date']
            
        msg += f"<b>{display_num}.</b> {formatted_date} - <b>{escape(item['name'])}</b>\n"
        row.append(InlineKeyboardButton(str(display_num), callback_data=f"edit_sel:{orig_idx}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
            
    if row:
        keyboard.append(row)
        
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ Prev 10", callback_data=f"edit_p:{page-1}"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next 10 ➡️", callback_data=f"edit_p:{page+1}"))
    if nav_row: keyboard.append(nav_row)
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query: await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = None 
    welcome_msg = (
        "Welcome to the Dental Foodlg telegram bot! 🦷😁\n\n"
        "I was created to help you reach your sugar goals and reduce the risk of forming tooth decay! High sugar intake gives the bacteria in your mouth lots of food. As the bacteria break down the sugar, they release acids that  can damage your teeth and cause decay. 😔 I hope to help you choose less sugary options and build up tooth-friendly habits! 💪\n\n"
        "<b>Food logging</b>\n"
        "/add to log your food entries\n"
        "/today to view today's dashboard\n"
        "/weekly to see your week's progress\n\n"
        "<b>Settings</b>\n"
        "/setgoal to amend your daily sugar limit\n"
        "/edit to manage recent entries\n"
        "/settings to edit smart reminders"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.HTML)
    
    all_data = load_data()
    uid = str(update.effective_user.id)
    if uid not in all_data or 'sugar_goal' not in all_data[uid]:
        await set_goal(update, context)

async def set_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'WAITING_FOR_GOAL'
    goal_msg = (
        "<b>Setting your sugar goal</b>\n\n"
        "To allow me to start tracking habits, do input your daily sugar goal in grams. WHO recommends <b>&lt;50g of sugar daily, and preferably &lt;25g of sugar</b>.\n\n <i>\"WHO advises limiting the intake of free sugars* to less than 10% of total energy intake – and ideally to less than 5% – to minimise the risk of dental caries throughout life. (This) is equivalent to 50 g (or about 12 level teaspoons) for a person of healthy body weight consuming about 2000 calories per day.\"</i>\n\n"
        "*Free sugars refer to all sugars added to foods or drinks, plus sugars naturally present in honey, syrups, and fruit juices. They are called 'free' because they are released from the food's cellular structure, making them easier to rapidly absorb.\n\n"
        "For more information, please visit:\n"
        "• https://www.who.int/news-room/fact-sheets/detail/sugars-and-dental-caries\n"
        "• https://www.who.int/news-room/fact-sheets/detail/healthy-diet\n\n"
        "👇 <b>Type your goal in numbers only (e.g., 50):</b>"
    )
    await update.message.reply_text(goal_msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'WAITING_FOR_FOOD'
    await update.message.reply_text(
        "📸 <b>Send a photo</b> or ⌨️ <b>Type a food name</b> to log your entry!",
        parse_mode=ParseMode.HTML
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    state = context.user_data.get('state')

    # --- HANDLE EDIT DATE/TIME INPUTS ---
    if state in ['WAIT_EDIT_DATE', 'WAIT_EDIT_TIME']:
        all_data = load_data()
        uid = str(update.effective_user.id)
        history = all_data.get(uid, {}).get('history', [])
        edit_index = context.user_data.get('edit_index')

        if edit_index is None or not 0 <= edit_index < len(history):
            context.user_data['state'] = None
            await update.message.reply_text("❌ Entry no longer exists.")
            return

        try:
            current_dt = datetime.strptime(history[edit_index]['date'], "%Y-%m-%d %H:%M")
            if state == 'WAIT_EDIT_DATE':
                new_date = datetime.strptime(user_text, "%Y-%m-%d").date()
                updated_dt = current_dt.replace(year=new_date.year, month=new_date.month, day=new_date.day)
                label = "Date"
            else:
                new_time = datetime.strptime(user_text, "%H:%M").time()
                updated_dt = current_dt.replace(hour=new_time.hour, minute=new_time.minute)
                label = "Time"
        except ValueError:
            expected_format = "YYYY-MM-DD" if state == 'WAIT_EDIT_DATE' else "HH:MM"
            await update.message.reply_text(f"❌ Invalid format. Please use {expected_format}.")
            return

        history[edit_index]['date'] = updated_dt.strftime("%Y-%m-%d %H:%M")
        save_data(all_data)
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ Entry {label.lower()} updated to {updated_dt.strftime('%Y-%m-%d %H:%M')}.")
        return

    # --- HANDLE SETTINGS TIME INPUTS ---
    if state in ['WAIT_MORN_TIME', 'WAIT_WEEK_TIME']:
        uid = str(update.effective_user.id)
        all_data = load_data()
        if uid not in all_data: all_data[uid] = {}
        
        user_val = user_text.strip()
        
        if state == 'WAIT_MORN_TIME':
            if user_val.upper() == 'OFF':
                all_data[uid]['morning_time'] = "OFF"
            else:
                try:
                    datetime.strptime(user_val, "%H:%M")
                    all_data[uid]['morning_time'] = user_val
                except ValueError:
                    await update.message.reply_text("❌ Invalid format. Please use HH:MM (e.g., 08:30). Try again:")
                    return
                    
        elif state == 'WAIT_WEEK_TIME':
            if user_val.upper() == 'OFF':
                all_data[uid]['weekly_time'] = "OFF"
            else:
                try:
                    day_str, time_str = user_val.split(" ")
                    if day_str.capitalize() not in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]: raise ValueError
                    datetime.strptime(time_str, "%H:%M")
                    all_data[uid]['weekly_time'] = f"{day_str.capitalize()} {time_str}"
                except ValueError:
                    await update.message.reply_text("❌ Invalid format. Please use 'Day HH:MM' (e.g., Sun 21:00). Try again:")
                    return

        save_data(all_data)
        schedule_user_jobs(context, uid, all_data[uid])
        context.user_data['state'] = None
        
        reply_markup = get_settings_keyboard(all_data[uid])
        await update.message.reply_text("✅ <b>Settings updated!</b>", reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    # --- 1. HANDLE GOAL SETTING ---
    if state == 'WAITING_FOR_GOAL':
        try:
            goal = float(user_text)
            if goal <= 0: raise ValueError
            all_data = load_data()
            uid = str(update.effective_user.id)
            if uid not in all_data: all_data[uid] = {}
            all_data[uid]['sugar_goal'] = goal
            save_data(all_data)
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ <b>Goal Set: {goal}g!</b>\nUse /add to start logging.", parse_mode=ParseMode.HTML)
        except: await update.message.reply_text("❌ Invalid number. Try again.")
        return

    # --- 2. HANDLE AI FEEDBACK ---
    elif state == 'WAITING_FOR_CORRECT_NAME':
        context.user_data['state'] = 'WAITING_FOR_FOOD'
        img_path = context.user_data.get('current_image_path')
        if img_path and os.path.exists(img_path):
            try:
                with open(TRAINING_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().isoformat(), img_path, user_text])
                await update.message.reply_text("💾 <b>Saved! Thanks for helping to improve our bot!</b>", parse_mode=ParseMode.HTML)
            except Exception as e: print(f"Logging error: {e}")
            
        matches = search_food_database(user_text, limit=50)
        if not matches: 
            await update.message.reply_text(f"🤔 No matches for '<b>{escape(user_text)}</b>'.", parse_mode=ParseMode.HTML)
            return
        
        context.user_data['db_matches'] = matches
        context.user_data['search_index'] = 0
        
        kb = [[InlineKeyboardButton(f['name'], callback_data=f"db:{i}")] for i, f in enumerate(matches[:5])]
        if len(matches) > 5:
            kb.append([InlineKeyboardButton("⬇️ More Options", callback_data="search_more")])
            
        await update.message.reply_text(f"🔎 <b>Here are the foodtypes found in our database. Is there something similar?</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    # --- 3. FOOD EDITING OR NORMAL LOGGING ---
    elif state in ['WAITING_FOR_FOOD', 'WAIT_EDIT_FOOD']:
        matches = search_food_database(user_text, limit=50)

        if not matches: 
            await update.message.reply_text(f"🤔 No matches for '<b>{escape(user_text)}</b>'.", parse_mode=ParseMode.HTML)
            return
        
        context.user_data['db_matches'] = matches
        context.user_data['search_index'] = 0
        
        keyboard = []
        for i, food in enumerate(matches[:5]):
            keyboard.append([InlineKeyboardButton(food['name'], callback_data=f"db:{i}")])
            
        if len(matches) > 5:
            keyboard.append([InlineKeyboardButton("⬇️ More Options", callback_data="search_more")])
            
        if state == 'WAIT_EDIT_FOOD':
            await update.message.reply_text(f"🔎 <b>Select the replacement food:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"🔎 Found {len(matches)} results for '<b>{escape(user_text)}</b>'.\nSelect one to log:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    else:
        await update.message.reply_text("I'm sorry, I don't quite understand what the command is. Would you like to see /start for the list of commands?")
    
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("ai:"):
        idx = int(data.split(":")[1])
        ai_guesses = context.user_data.get('ai_guesses', [])
        if idx < len(ai_guesses):
            matches = search_food_database(ai_guesses[idx], limit=50)
            context.user_data['db_matches'] = matches
            context.user_data['search_index'] = 0
            
            kb = [[InlineKeyboardButton(f['name'], callback_data=f"db:{i}")] for i, f in enumerate(matches[:5])]
            if len(matches) > 5:
                kb.append([InlineKeyboardButton("⬇️ More Options", callback_data="search_more")])
                
            await query.edit_message_text(f"✅ AI Identified: {ai_guesses[idx]}\n👇 Select specific item:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == "ai_wrong":
        context.user_data['state'] = 'WAITING_FOR_CORRECT_NAME'
        await query.edit_message_text(
            "📝 <b>Help us improve!</b>\n\n"
            "What is the name of this food?\n"
            "<i>Type it below:</i>",
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("db:"):
        idx = int(data.split(":")[1])
        matches = context.user_data.get('db_matches', [])
        if idx < len(matches):
            food = matches[idx]
            
            sugar = float(food.get('Sugar', 0)) if food.get('Sugar') != 'N/A' else 0.0
            cal = float(food.get('Energy', 0)) if food.get('Energy') != 'N/A' else 0.0
            prot = float(food.get('Protein', 0)) if food.get('Protein') != 'N/A' else 0.0
            fat = float(food.get('TotalFat', 0)) if food.get('TotalFat') != 'N/A' else 0.0
            food_id = food.get('id')
            
            all_data = load_data()
            uid = str(update.effective_user.id)
            if uid not in all_data: all_data[uid] = {}
            if 'history' not in all_data[uid]: all_data[uid]['history'] = []

            if context.user_data.get('state') == 'WAIT_EDIT_FOOD':
                orig_idx = context.user_data.get('edit_index')
                old_date = all_data[uid]['history'][orig_idx]['date'] 
                
                all_data[uid]['history'][orig_idx] = {
                    'id': food_id, 'name': food['name'], 'date': old_date,
                    'calories': cal, 'sugar': sugar, 'protein': prot, 'fat': fat
                }
                save_data(all_data)
                context.user_data['state'] = None
                await query.edit_message_text(
                    f"✅ <b>Successfully updated food to: {escape(food['name'])}</b>\n\n"
                    f"🍬 <b>Sugar:</b> {sugar}g\n"
                    f"🔥 <b>Calories:</b> {cal}\n"
                    f"💪 <b>Protein:</b> {prot}g\n"
                    f"🥑 <b>Total Fat:</b> {fat}g",
                    parse_mode=ParseMode.HTML
                )
                return 
            else:
                current_dt = datetime.now()
                all_data[uid]['last_active'] = current_dt.strftime("%Y-%m-%d %H:%M:%S") 

                all_data[uid]['history'].append({
                    'id': food_id, 'name': food['name'],
                    'date': current_dt.strftime("%Y-%m-%d %H:%M"),
                    'calories': cal, 'sugar': sugar, 'protein': prot, 'fat': fat
                })
                save_data(all_data)
            
            user_goal = all_data[uid].get('sugar_goal', DEFAULT_GOAL)
            today_sums = get_daily_sums(all_data[uid]['history'], datetime.now().strftime("%Y-%m-%d"))
            bar_str, pct = generate_progress_bar(today_sums['sugar'], user_goal)
            
            msg = (
                f"✅ <b>Logged: {escape(food['name'])}</b>\n\n"
                f"🍬 <b>Sugar:</b> {sugar}g\n"
                f"🔥 <b>Calories:</b> {cal}\n"
                f"💪 <b>Protein:</b> {prot}g\n"
                f"🥑 <b>Total Fat:</b> {fat}g\n"
            )
            
            advice = get_sugar_advice_for_food(food_id)
            if advice:
                msg += f"\n<i>{escape(advice)}</i>\n"

            msg += (
                f"\n<b>Current sugars:</b>\n"
                f"🍬 <b>Today:</b> {today_sums['sugar']:.1f}g / {user_goal}g\n"
                f"{bar_str} {pct}%"
            )

            kb = [[InlineKeyboardButton("🕒 Recorded late?", callback_data="late_start")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            await check_realtime_nudges(update, context, sugar)
            
            # Start customized background jobs if they aren't running yet!
            if not context.job_queue.get_jobs_by_name(f"morn_{uid}"):
                schedule_user_jobs(context, uid, all_data[uid])

    elif data == "search_more":
        matches = context.user_data.get('db_matches', [])
        current_index = context.user_data.get('search_index', 0)
        new_index = current_index + 5
        context.user_data['search_index'] = new_index
        
        keyboard = []
        for i, food in enumerate(matches[new_index : new_index + 5], start=new_index):
            keyboard.append([InlineKeyboardButton(food['name'], callback_data=f"db:{i}")])
            
        if new_index + 5 < len(matches):
            keyboard.append([InlineKeyboardButton("⬇️ More Options", callback_data="search_more")])
            
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_search")])
            
        try: await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e: print(f"Error updating search pagination: {e}")

    elif data == "cancel_search":
        context.user_data.pop('db_matches', None)
        context.user_data.pop('search_index', None)
        await query.message.delete()

    elif data == "late_start":
        await query.edit_message_text("📅 <b>When did you eat this?</b>", reply_markup=create_past_days_keyboard(), parse_mode=ParseMode.HTML)

    elif data.startswith("late_date:"):
        offset = int(data.split(":")[1])
        all_data = load_data()
        uid = str(update.effective_user.id)
        history = all_data.get(uid, {}).get('history', [])
        
        if history:
            last_item = history[-1]
            target_date = datetime.now() - timedelta(days=offset)
            current_time = datetime.now().time()
            new_dt = datetime.combine(target_date.date(), current_time)
            last_item['date'] = new_dt.strftime("%Y-%m-%d %H:%M")
            save_data(all_data)
            date_label = "Today" if offset == 0 else new_dt.strftime("%d %b")
            await query.edit_message_text(f"✅ <b>Entry Updated!</b>\nmoved to: <b>{date_label}</b>", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("❌ Error: No recent entry found.")

    elif data.startswith("del:"):
        display_index = int(data.split(":")[1]) 
        all_data = load_data()
        uid = str(update.effective_user.id)
        history = all_data.get(uid, {}).get('history', [])
        if not history: await query.edit_message_text("❌ History is empty."); return
        real_index = -1 * display_index
        try:
            if abs(real_index) <= len(history):
                removed_item = history.pop(real_index)
                save_data(all_data)
                await query.edit_message_text(f"🗑️ <b>Deleted:</b> {escape(removed_item['name'])}", parse_mode=ParseMode.HTML)
            else: await query.edit_message_text("❌ Item not found.")
        except Exception as e: await query.edit_message_text(f"❌ Error: {str(e)}")

    elif data.startswith("edit_p:"):
        page = int(data.split(":")[1])
        await show_edit_page(update, context, page)
        
    # --- SETTINGS NAVIGATION ---
    elif data == "toggle_meal":
        all_data = load_data()
        uid = str(update.effective_user.id)
        if uid not in all_data: all_data[uid] = {}
        all_data[uid]['reminders_mealtime'] = not all_data[uid].get('reminders_mealtime', True)
        save_data(all_data)
        try: await query.edit_message_reply_markup(reply_markup=get_settings_keyboard(all_data[uid]))
        except: pass
        
    elif data == "edit_morn":
        context.user_data['state'] = 'WAIT_MORN_TIME'
        await query.edit_message_text(
            "🌅 <b>Set Morning Bump Time</b>\n\n"
            "Please type the new time in 24-hour <code>HH:MM</code> format (e.g., <code>08:30</code>).\n"
            "<i>Type 'OFF' to disable.</i>", 
            parse_mode=ParseMode.HTML
        )
        
    elif data == "edit_week":
        context.user_data['state'] = 'WAIT_WEEK_TIME'
        await query.edit_message_text(
            "📅 <b>Set Weekly Report Time</b>\n\n"
            "Please type the new day and time (e.g., <code>Sun 21:00</code> or <code>Mon 08:00</code>).\n"
            "<i>Type 'OFF' to disable.</i>", 
            parse_mode=ParseMode.HTML
        )
        
    elif data.startswith("edit_sel:"):
        orig_idx = int(data.split(":")[1])
        all_data = load_data()
        uid = str(update.effective_user.id)
        history = all_data.get(uid, {}).get('history', [])
        
        if 0 <= orig_idx < len(history):
            item = history[orig_idx]
            msg = (
                f"🛠 <b>Modifying Entry</b>\n\n"
                f"🍔 <b>Food:</b> {escape(item['name'])}\n"
                f"📅 <b>Date/Time:</b> {item['date']}\n"
                f"🍬 <b>Sugar:</b> {item.get('sugar', 0)}g\n\n"
                f"What would you like to modify?"
            )
            kb = [
                [InlineKeyboardButton("📅 Modify Date", callback_data=f"edit_dt:{orig_idx}"),
                 InlineKeyboardButton("⏰ Modify Time", callback_data=f"edit_tm:{orig_idx}")],
                [InlineKeyboardButton("🍔 Modify Food", callback_data=f"edit_fd:{orig_idx}"),
                 InlineKeyboardButton("❌ Delete", callback_data=f"edit_del:{orig_idx}")],
                [InlineKeyboardButton("🔙 Back to List", callback_data="edit_p:0")]
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("❌ Item no longer exists.")
            
    elif data.startswith("edit_del:"):
        orig_idx = int(data.split(":")[1])
        all_data = load_data()
        uid = str(update.effective_user.id)
        history = all_data.get(uid, {}).get('history', [])
        
        if 0 <= orig_idx < len(history):
            removed = history.pop(orig_idx)
            save_data(all_data)
            await query.edit_message_text(f"🗑️ <b>Deleted:</b> {escape(removed['name'])}", parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text("❌ Item no longer exists.")

    elif data.startswith("edit_dt:"):
        orig_idx = int(data.split(":")[1])
        context.user_data['state'] = 'WAIT_EDIT_DATE'
        context.user_data['edit_index'] = orig_idx
        await query.edit_message_text("📅 <b>Modify Date</b>\n\nPlease type the new date in <code>YYYY-MM-DD</code> format (e.g., 2024-05-20).", parse_mode=ParseMode.HTML)

    elif data.startswith("edit_tm:"):
        orig_idx = int(data.split(":")[1])
        context.user_data['state'] = 'WAIT_EDIT_TIME'
        context.user_data['edit_index'] = orig_idx
        await query.edit_message_text("⏰ <b>Modify Time</b>\n\nPlease type the new time in 24-hour <code>HH:MM</code> format (e.g., 14:30).", parse_mode=ParseMode.HTML)

    elif data.startswith("edit_fd:"):
        orig_idx = int(data.split(":")[1])
        context.user_data['state'] = 'WAIT_EDIT_FOOD'
        context.user_data['edit_index'] = orig_idx
        await query.edit_message_text("🍔 <b>Modify Food Item</b>\n\nPlease type the name of the new food you want to search for.", parse_mode=ParseMode.HTML)
    

async def check_realtime_nudges(update, context, new_sugar_amount):
    all_data = load_data()
    user_data = all_data.get(str(update.effective_user.id), {})
    user_goal = user_data.get('sugar_goal', DEFAULT_GOAL)
    current_total = get_daily_sums(user_data.get('history', []), datetime.now().strftime("%Y-%m-%d"))['sugar']
    if current_total > user_goal:
        await update.callback_query.message.reply_text(f"🚨 <b>Sugar Alert!</b>\nExceeded limit! ({current_total:.1f}g / {user_goal}g)\nCut down on sugar! 🦷", parse_mode=ParseMode.HTML)

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = None 
    await show_edit_page(update, context, page=0)

async def test_morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 <b>Dev Mode:</b> Forcing the morning bump to run in 1 second...", parse_mode=ParseMode.HTML)
    context.job_queue.run_once(daily_morning_check, when=1, chat_id=update.effective_chat.id)

async def test_weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 <b>Dev Mode:</b> Forcing the weekly report to run in 1 second...", parse_mode=ParseMode.HTML)
    context.job_queue.run_once(
        weekly_custom_job, 
        when=1, 
        chat_id=update.effective_chat.id
    )

async def sample_meal_nudge(context: ContextTypes.DEFAULT_TYPE):
    await send_mealtime_nudge(context, context.job.chat_id, is_test=True)

async def test_meal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_data = load_data()
    uid = str(update.effective_user.id)
    history = all_data.get(uid, {}).get('history', [])
    
    habit_hours = predict_user_habits(history)
    
    if not habit_hours:
        msg = "🛠 <b>Dev Mode:</b> Not enough history to predict mealtimes yet.\n\nTriggering a single sample nudge anyway in 1 second..."
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        context.job_queue.run_once(
            sample_meal_nudge, 
            when=1, 
            chat_id=update.effective_chat.id
        )
    else:
        formatted_hours = []
        for h in habit_hours:
            ampm = "AM" if h < 12 else "PM"
            display_h = h if h <= 12 else h - 12
            if display_h == 0: display_h = 12
            formatted_hours.append(f"{display_h}:00 {ampm}")
            
        msg = f"🛠 <b>Dev Mode:</b>\nBased on your history, the bot predicts you eat around: <b>{', '.join(formatted_hours)}</b>.\n\nTriggering {len(formatted_hours)} sample nudges in sequence..."
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        
        # Loop through the list and send a nudge for EACH predicted mealtime
        for i in range(len(habit_hours)):
            context.job_queue.run_once(
                sample_meal_nudge, 
                when=i + 1,  # Staggers them 1 second apart (1s, 2s, 3s...)
                chat_id=update.effective_chat.id
            )

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = None 
    
    all_data = load_data()
    user_data = all_data.get(str(update.effective_user.id), {})
    history = user_data.get('history', [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # --- SIMPLIFIED STREAK BAR ---
    streak = user_data.get('streak', 0)
    streak_display = get_streak_display(streak)
        
    pretty_date = datetime.now().strftime("%d %b")
    today_items = [h for h in history if h['date'].startswith(today_str)]
    today_sums = get_daily_sums(history, today_str)
    goal = user_data.get('sugar_goal', DEFAULT_GOAL)
    bar_str, pct = generate_progress_bar(today_sums['sugar'], goal)
    
    msg = (
        f"📊 <b>Today's Nutrition ({pretty_date})</b>\n"
        f"{streak_display}\n\n"
        f"{bar_str} {pct}%\n"
    )
    
    if not today_items: 
        msg += "<i>No food logged yet.</i>"
    else:
        for i, item in enumerate(today_items):
            try: 
                dt = datetime.strptime(item['date'], "%Y-%m-%d %H:%M")
                time_str = dt.strftime("%I:%M%p").lower().lstrip('0')
            except: 
                time_str = "?"
            msg += f"<b>{i+1}. {escape(item['name'])}</b> ({item['sugar']}g sugar)\n<i>{time_str}</i>\n"
            
        worst_food = max(today_items, key=lambda x: float(x.get('sugar', 0)))
        fid = worst_food.get('id')
        
        if fid:
            advice = get_sugar_advice_for_food(fid)
            if advice:
                msg += f"\n💡 <b>Tip for {escape(worst_food['name'])}:</b>\n<i>{escape(advice)}</i>"
            if not advice:
                advice = get_random_sugar_advice()
                msg += f"\n💡 <i>{escape(advice)}</i>"
                
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def weekly_report_cmd(update, context):
    context.user_data['state'] = None
    all_data = load_data()
    
    uid = str(update.effective_user.id)
    user_data = all_data.get(uid, {})
    history = user_data.get('history', [])
    sugar_goal = user_data.get('sugar_goal', DEFAULT_GOAL) 
    
    if not history: 
        await update.message.reply_text("📉 Not enough data yet."); return
    
    avg, graph, top_3 = get_weekly_analysis(history, sugar_goal)
    
    msg = f"📊 <b>Weekly Overview</b> 📊\nAvg Sugar: {avg:.1f}g/day\n\n{graph}\n<b>🏆 Top Sugar Spikes:</b>\n"
    for i, item in enumerate(top_3):
        msg += f"{i+1}. <b>{escape(item['name'])}</b> ({item['sugar']}g)\n"
    
    if top_3:
        worst_food = top_3[0]
        fid = worst_food.get('id')
        
        info = None
        if fid: info = get_group_info_for_food(fid)
        if not info: info = get_random_group_info()
            
        msg += f"\n💡 <b>Did you know?:</b>\n<i>{escape(info)}</i>"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def handle_photo(update, context):

    if context.user_data.get('state') != 'WAITING_FOR_FOOD':
        await update.message.reply_text("Please use /add first before sending a photo!")
        return

    await update.message.reply_text("Analyzing...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        filename = f"{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
        file_path = os.path.join(TRAINING_DIR, filename)
        
        await photo_file.download_to_drive(file_path)
        context.user_data['current_image_path'] = file_path

        with open(file_path, 'rb') as img_file:
            resp = requests.post(FOOD_API_URL, files={'image': ('f.jpg', img_file, 'image/jpeg')}, headers=API_HEADERS)
        
        guesses = parse_ai_response(resp)
        if not guesses: 
            await update.message.reply_text("No food found.")
            return

        context.user_data['ai_guesses'] = guesses
        
        kb = [[InlineKeyboardButton(g, callback_data=f"ai:{i}")] for i, g in enumerate(guesses[:5])]
        kb.append([InlineKeyboardButton("❌ Food not shown?", callback_data="ai_wrong")])
        
        await update.message.reply_text("🤔 <b>AI thinks it's:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    except Exception as e: 
        await update.message.reply_text(f"Error: {e}")

async def send_mealtime_nudge(context: ContextTypes.DEFAULT_TYPE, chat_id: str, is_test: bool = False):
    """Builds and sends the mealtime nudge. Used by both the real job and the test command."""
    advice = get_daily_tip()
    
    # Add a test flag to the title if triggered manually
    title = "🔔 <b>Did you just eat? (TEST)</b>" if is_test else "🔔 <b>Did you just eat?</b>"
    
    msg = (
        f"{title}\n\n"
        f"Don't forget to log your meal!\n\n"
        f"💡 <b>Quick Tip:</b>\n"
        f"<i>{escape(advice)}</i>"
    )
    
    try:
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Error sending meal nudge: {e}")

async def daily_morning_check(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    all_data = load_data()
    user_id_str = str(chat_id)
    user_data = all_data.get(user_id_str, {})
    
    user_goal = user_data.get('sugar_goal', DEFAULT_GOAL)
    history = user_data.get('history', [])
    
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_total = get_daily_sums(history, yesterday_str)['sugar']
    
    current_streak = user_data.get('streak', 0)

    if yesterday_total > user_goal:
        current_streak = 0 
        msg = f"🌅 <b>Yesterday</b>\n❌ Missed: {yesterday_total:.1f}g / {user_goal}g.\n\n<i>You've got this! 💪</i>"
    else:
        current_streak += 1 
        msg = f"🌅 <b>Yesterday</b>\n✅ Smashed: {yesterday_total:.1f}g / {user_goal}g.\n<i>Great job! 🦷 Keep working at it!</i>"

    streak_display = get_streak_display(current_streak)
    msg += f"\n\n{streak_display}"
        
    if user_id_str not in all_data: 
        all_data[user_id_str] = {}
    all_data[user_id_str]['streak'] = current_streak
    save_data(all_data)

    daily_tip = get_daily_tip()
    msg += f"\n\n💡 <b>Tip of the Day:</b>\n<i>{escape(daily_tip)}</i>"

    try: await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
    except Exception as e: print(f"Error sending morning message: {e}")
        
async def smart_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    all_data = load_data()
    current_hour = datetime.now().hour

    for user_id, data in all_data.items():
        if not data.get('reminders_mealtime', True): 
            continue 
            
        history = data.get('history', [])
        last_active_str = data.get('last_active')
        
        if not last_active_str and history:
            last_active_str = history[-1].get('date') + ":00" 

        if last_active_str:
            try:
                last_active_time = datetime.strptime(last_active_str, "%Y-%m-%d %H:%M:%S")
                hours_since_active = (datetime.now() - last_active_time).total_seconds() / 3600
                if hours_since_active < 12.0: continue 
            except: pass

        habit_hours = predict_user_habits(history)
        
        should_notify = False
        for h in habit_hours:
            if current_hour == h + 1: 
                should_notify = True
                break
        
        if should_notify:
            await send_mealtime_nudge(context, user_id, is_test=False)

async def weekly_custom_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = str(context.job.chat_id)
    all_data = load_data()
    data = all_data.get(user_id, {})
    
    history = data.get('history', [])
    if not history: return
    
    sugar_goal = data.get('sugar_goal', DEFAULT_GOAL)
    avg, graph, top_3 = get_weekly_analysis(history, sugar_goal)
    
    msg = f"👋 <b>Weekly Sugar Bump!</b>\nAvg: {avg:.1f}g\n{graph}"
    
    if top_3:
         worst = top_3[0]
         msg += f"\n\nHighest Spike: <b>{escape(worst['name'])}</b> ({worst['sugar']}g)"
         fid = worst.get('id')
         info = get_group_info_for_food(fid) if fid else None
         if not info: info = get_random_group_info()
         msg += f"\n💡 <i>{escape(info)}</i>"
        
    msg += f"\n\nWhat is one small step you can make to reduce your sugar consumption next week?"
    try: await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
    except: pass

async def set_streak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_streak = int(context.args[0])
        if new_streak < 0: raise ValueError
        
        all_data = load_data()
        uid = str(update.effective_user.id)
        if uid not in all_data: all_data[uid] = {}
        
        all_data[uid]['streak'] = new_streak
        save_data(all_data)
        
        display = get_streak_display(new_streak)
        await update.message.reply_text(f"🛠 <b>Dev Mode:</b> Streak set to {new_streak}!\n\nPreview:\n{display}", parse_mode=ParseMode.HTML)
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: `/setstreak <number>` (e.g., /setstreak 15)", parse_mode=ParseMode.MARKDOWN)

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the interactive settings panel."""
    all_data = load_data()
    uid = str(update.effective_user.id)
    if uid not in all_data: all_data[uid] = {}
        
    msg = (
        "⚙️ <b>Notification Settings</b>\n\n"
        "Here are the various bumps within the bot to help remind you of sugar intake. Click to change their timings!\n\n"
        "🌅 <b>Morning Bump</b> - Sends a daily recap of yesterday's sugar intake and a reflection on your top sugar spike.\n\n"
        "📅 <b>Weekly Report</b> - Sends a weekly overview of your food and sugars.\n\n"
        "🍽 <b>Mealtime nudges</b> - Analyses your past history of mealtimes and bumps you if it hasn't noted a recorded meal."
    )
    reply_markup = get_settings_keyboard(all_data[uid])
    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def main_async():
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        print(f"\n🔍 DEBUG: Looking for .env file at: {env_path}")
        print(f"🔍 DEBUG: Does the .env file exist here? {os.path.exists(env_path)}")
        if os.path.exists(env_path):
            print("🔍 DEBUG: The file exists, but the token wasn't found. Check for typos or extra spaces in your .env file!")
        print("\n")
        raise ValueError("🚨 ERROR: TELEGRAM_BOT_TOKEN is missing! Please check your .env file.")
        
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setgoal", set_goal))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("today", today_cmd))
    application.add_handler(CommandHandler("edit", edit_cmd))
    application.add_handler(CommandHandler("weekly", weekly_report_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_click))

    # Background
    application.job_queue.run_repeating(smart_reminder_job, interval=3600, first=10)
    
    application.add_handler(CommandHandler("testmorning", test_morning_cmd))
    application.add_handler(CommandHandler("testweekly", test_weekly_cmd))
    application.add_handler(CommandHandler("testmeal", test_meal_cmd))
    application.add_handler(CommandHandler("setstreak", set_streak_cmd))

    # LOAD CUSTOM JOBS ON STARTUP ---
    all_data = load_data()
    for uid, udata in all_data.items():
        schedule_user_jobs(application, uid, udata)

    print("🤖 Bot is running...")
    await application.initialize()
    await application.start()
    await application.bot.delete_webhook(drop_pending_updates=True) 
    await application.updater.start_polling()
    stop_signal = asyncio.Future()
    try: await stop_signal
    except KeyboardInterrupt: pass
    finally: await application.stop()

if __name__ == '__main__':
    try: asyncio.run(main_async())
    except KeyboardInterrupt: pass