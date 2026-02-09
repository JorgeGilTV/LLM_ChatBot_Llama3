# 📊 Datadog Integration Setup Guide

## Overview

The Datadog Dashboards tool allows you to list, search, and access your Datadog dashboards directly from the Arlo GenAI interface.

## 🔑 Getting Your Datadog Credentials

### Step 1: Get API Key

1. Log in to your Datadog account at: https://arlo.datadoghq.com
2. Go to **Organization Settings** → **API Keys**
   - Or direct link: https://arlo.datadoghq.com/organization-settings/api-keys
3. Click **"New Key"** or use an existing one
4. Copy the API Key value

### Step 2: Get Application Key

1. In the same **Organization Settings** page, go to **Application Keys**
   - Or direct link: https://arlo.datadoghq.com/organization-settings/application-keys
2. Click **"New Key"**
3. Give it a name (e.g., "Arlo GenAI Integration")
4. Copy the Application Key value

### Step 3: Identify Your Datadog Site

Based on your URL, your Datadog site is likely:
- `datadoghq.com` (US1) - if your URL is `app.datadoghq.com`
- `us3.datadoghq.com` (US3)
- `us5.datadoghq.com` (US5)
- `datadoghq.eu` (EU1)

Since your URL is `arlo.datadoghq.com`, use: **`datadoghq.com`**

## ⚙️ Configuration

### Option 1: Update `.env` file (Local Development)

Edit your `.env` file:

```bash
# Datadog Credentials
DATADOG_API_KEY="your_api_key_here"
DATADOG_APP_KEY="your_application_key_here"
DATADOG_SITE="datadoghq.com"
```

After saving, restart the server:
```bash
# If running locally
pkill -f "python3 app.py"
python3 app.py

# If using Docker
docker-compose restart
```

### Option 2: Update Docker Environment (Production)

If using Docker, update your `docker-compose.yml` or `.env` file, then:

```bash
docker-compose down
docker-compose up -d
```

## 🚀 Using the Datadog Dashboards Tool

### 1. Access the Application
Open: http://localhost:5001

### 2. Select the Tool
Check the box for **"Datadog_Dashboards"**

### 3. Search Dashboards

**Show all dashboards:**
- Leave the search box empty and click "Send"

**Search by name:**
- Enter keywords like:
  - `RED` - Find RED metrics dashboard
  - `API` - Find API-related dashboards
  - `monitoring` - Find monitoring dashboards
  - `mpd-2aw-sfe` - Find specific dashboard by ID

### 4. View Results

The tool will display:
- ✅ **Dashboard Title** - Name of the dashboard
- ✅ **Dashboard ID** - Unique identifier
- ✅ **Type** - Layout type (ordered, free, etc.)
- ✅ **Author** - Who created it
- ✅ **Link** - Direct link to open in Datadog

Results will be highlighted if they match your search term.

## 🎯 Example Searches

```
Search: "RED"
Result: Shows all dashboards with "RED" in the title
Example: "RED - Metrics", "RED Monitoring Dashboard"

Search: "api"
Result: Shows API-related dashboards
Example: "API Performance", "Backend API Metrics"

Search: "" (empty)
Result: Shows ALL dashboards in your account
```

## 🔒 Permissions Required

Your Datadog API and Application keys need the following permissions:
- `dashboards_read` - To list and view dashboard information

If you get a 403 error, verify your keys have the correct permissions in Datadog.

## 🐛 Troubleshooting

### Error: "Datadog credentials not found"
- Check that your `.env` file contains `DATADOG_API_KEY` and `DATADOG_APP_KEY`
- Verify the file is in the correct location
- Restart the server after adding credentials

### Error: 401 Authentication Failed
- Verify your API Key and Application Key are correct
- Check for extra spaces or quotes in your `.env` file
- Make sure you're using the correct keys from your Datadog account

### Error: 403 Access Forbidden
- Your keys don't have the required permissions
- Go to Datadog → Organization Settings → API Keys
- Verify the key has `dashboards_read` permission
- Create a new key with proper permissions if needed

### No dashboards showing
- Verify you have dashboards in your Datadog account
- Check the search term isn't too restrictive
- Try leaving the search empty to see all dashboards

## 📖 Additional Resources

- [Datadog API Documentation](https://docs.datadoghq.com/api/latest/)
- [Dashboard API Reference](https://docs.datadoghq.com/api/latest/dashboards/)
- [Authentication Guide](https://docs.datadoghq.com/account_management/api-app-keys/)

## 🎨 Features

- ✅ **List all dashboards** - See your entire dashboard library
- ✅ **Search by name** - Quickly find specific dashboards
- ✅ **Search by ID** - Locate dashboards by their unique ID
- ✅ **Direct links** - Click to open dashboards in Datadog
- ✅ **Dashboard metadata** - See type, author, and more
- ✅ **Highlighted results** - Search terms are highlighted in results

## 💡 Tips

1. **Bookmark common searches** - Use the search history feature
2. **Use partial matches** - Search for "red" will match "RED - Metrics"
3. **Copy dashboard IDs** - Useful for API automation
4. **Combine with other tools** - Use alongside version checks and status monitors
