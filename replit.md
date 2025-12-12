# YouTube Music Telegram Bot

## Overview
A Python Telegram bot that searches YouTube Music and displays results with inline navigation and download options.

## Project Structure
- `main.py` - Main bot logic with handlers for commands, messages, and callbacks
- `requirements.txt` - Python dependencies

## Features
- `/start` command greets users and prompts for song name
- Search YouTube Music using ytmusicapi
- Display 4-5 top results one at a time
- Each result shows: title, artist, album, duration, and thumbnail
- Inline keyboard navigation (Previous, Next, Download Song, Restart)
- Per-user state management for search results and current index
- Download songs as MP3 files directly to Telegram using yt-dlp

## Environment Variables
- `TELEGRAM_BOT_TOKEN` - Your Telegram Bot API token (required)

## How to Get a Telegram Bot Token
1. Open Telegram and search for @BotFather
2. Send `/newbot` command
3. Follow instructions to name your bot
4. Copy the token provided

## Running the Bot
The bot runs using polling mode and will continuously listen for messages.

## Dependencies
- python-telegram-bot (v20+) - Telegram Bot API wrapper with async support
- ytmusicapi - YouTube Music API client
- yt-dlp - YouTube audio downloader
- ffmpeg - Audio processing (system dependency)
- requests - HTTP library

## Recent Changes
- Added MP3 download functionality using yt-dlp (December 2025)
- Initial project setup (December 2025)
