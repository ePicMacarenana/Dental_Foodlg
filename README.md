# Dental Foodlg Telegram Bot

This is a Python-based Telegram bot designed to help users track their daily sugar intake, log food via text or AI image recognition, and build tooth-friendly dietary habits to prevent dental decay.

## 1. Tool Versions & Prerequisites

To run this application, you will need the following installed on your machine or deployment server:

* **Python:** Version 3.8 or higher
* **pip:** Python package installer
* **OS:** Windows, macOS, or Linux

## 2. Project Structure

Ensure all of the following files are located in the same root directory before running the bot:

* `Project.py` (Main application script)
* `requirements.txt` (Python dependencies)
* `.env` (Hidden environment variables file containing the bot token)
* `foodtypes.json` (Food nutritional database)
* `foodIDtoAdviceGroup.json` (Maps individual foodID to Advice Groups)
* `sugarAdviceExtraInfo.json` (Branches out Advice Groups to multiple general group info)
* `sugarAdvice.json` (Branches out Advice Groups to multiple specific sugar advice)
* `zh.json` (Language and text map containing bot responses)

*(Note: The `user_data.json` and `training_data/` folder will be generated automatically by the bot upon first run.)*

## 3. How to Connect the App to Telegram

To connect this code to a live Telegram bot, you must use your own **Bot Token** securely via an Environment Variable.

### Step A: Get your Token from BotFather

1. Open Telegram and search for `@BotFather`.
2. Send the command `/newbot` and follow the prompts to name your bot.
3. BotFather will provide an HTTP API Token. Copy this token.

### Step B: Setup the hidden .env File

1. In the root directory of this project, create a new text file and name it `[name].env`
2. Open the file in a text editor and add the following line, pasting your token after the equals sign:
   ```
   TELEGRAM_BOT_TOKEN=paste_your_token_here
   ```
3. Save the file. **Never share this file or upload it to a public repository (like GitHub).**

## 4. Setup & Installation Instructions

**Step 1: Install Dependencies**
Install the required tools specified in the requirements file:
```
pip install -r requirements.txt
```

**Step 2: Run the Bot**
Start the application:
```
python Project.py
```
*(Use `python3 Project.py` on Mac/Linux if required).*

You should see the message `🤖 Bot is running...` in your terminal. The bot is up and functional.

NOTE: You may choose to set up a virtual environment in your first setup.

## Developer tools

There are 2 hidden commands that developers can use to test out certain functions of the bot.

* /testmorning: This will force the bot to send the daily bump (that the user will set the timing), so that you can verify the contents of the message. Do be aware that this causes the streak to +1.
* /setstreak: This will change the streak date for you to verify the formatting. Ideas to create a reward system at higher streaks is a possibility in the future.
* /testweekly: This will force the bot to send the weekly bump (that the user will set the timing).
* /testmeal: This will force the bot to send the mealtime bump, and the predicted mealtime.
