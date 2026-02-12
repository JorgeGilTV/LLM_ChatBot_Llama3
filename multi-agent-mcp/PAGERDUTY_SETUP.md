# 🚨 PagerDuty Integration Setup

This guide explains how to set up PagerDuty integration with **GOC_AgenticAI**.

## 🎯 Overview

GOC_AgenticAI includes a comprehensive PagerDuty integration with:
- **Real-Time Status Monitor**: Auto-refresh card in main area (every 3 minutes)
- **3 Specialized Tools**: Incidents list, Analytics dashboard, Insights & trends
- **Full API Pagination**: Accurate counts for all incidents (up to 1000)
- **Clickable Incidents**: Direct links from status card to PagerDuty
- **Interactive Charts**: Chart.js visualizations for analytics

## Prerequisites

- PagerDuty account with API access
- Admin or appropriate permissions to create API tokens

## Step 1: Generate PagerDuty API Token

1. **Log in to PagerDuty**
   - Go to https://[your-subdomain].pagerduty.com

2. **Navigate to API Access**
   - Click on your profile picture (top right)
   - Select **"User Settings"**
   - Go to **"User API Tokens"** tab

3. **Create a New API Token**
   - Click **"Create API User Token"**
   - Give it a descriptive name: `GOC_AgenticAI_Integration`
   - Click **"Create Token"**

4. **Copy the Token**
   - ⚠️ **Important**: Copy the token immediately - it won't be shown again!
   - Keep it secure - treat it like a password

## Step 2: Configure Environment Variable

1. **Open your `.env` file**
   ```bash
   cd /path/to/multi-agent-mcp
   nano .env
   ```

2. **Add the PagerDuty token**
   ```bash
   # PagerDuty API Token
   PAGERDUTY_API_TOKEN=your_actual_pagerduty_api_token_here
   ```

3. **Save and close** the file

## Step 3: Restart the Application

If using Docker:
```bash
docker-compose restart arlo-agenticai
```

If running locally:
```bash
# Kill the current process
lsof -ti:8080 | xargs kill -9

# Restart the app
python3 app.py
```

## Step 4: Verify Integration

1. **Open the web interface**: http://localhost:8080

2. **Check PagerDuty Status Card** (Main Area):
   - Located next to "How to use" section
   - Should display real-time incident counts
   - Auto-refreshes every 3 minutes
   - Shows:
     - 🔴 Triggered incidents count
     - 🟡 Acknowledged incidents count
     - 🟢 Resolved incidents count (last 7 days)
     - Top 5 active incidents
     - Last 5 resolved incidents
   - Click any incident to open in PagerDuty

3. **Test PagerDuty Tools**:
   - You should see three checkboxes:
     - **"PagerDuty"** - For detailed incidents list
     - **"PagerDuty_Dashboards"** - For analytics dashboard
     - **"PagerDuty_Insights"** - For incident activity insights
   - Click any checkbox and click **"Send"**
   - You should see data from your PagerDuty account

## Usage

### PagerDuty Incidents (List View)

#### Basic Search
- Select **"PagerDuty"** checkbox
- Leave the search box empty to see all incidents (last 7 days)
- Click **"Send"**

#### Filter by Service
- Select **"PagerDuty"** checkbox
- Type a service name in the search box (e.g., "streaming-service")
- Click **"Send"**

#### Filter by Incident Number
- Select **"PagerDuty"** checkbox
- Type an incident number (e.g., "12345")
- Click **"Send"**

### PagerDuty Dashboards (Analytics View)

#### View Analytics Dashboard
- Select **"PagerDuty_Dashboards"** checkbox
- Click **"Send"** (no search text needed)
- View interactive charts and metrics for the last 30 days

### PagerDuty Insights (Trends & Activity Report)

#### View Insights Report
- Select **"PagerDuty_Insights"** checkbox
- Click **"Send"** (no search text needed)
- View detailed insights including:
  - Average resolution time
  - Busiest days and hours
  - Top users by incident count
  - Resolution time percentiles
  - Incident patterns and trends

## Features

### PagerDuty Status Monitor (Auto-Refresh)
The real-time status card in the main area provides:

- 🔄 **Auto-Refresh**: Updates every 3 minutes automatically
- 🎯 **Prominent Position**: Located next to "How to use" section
- 🚦 **Visual Traffic Light**: Three-column status display with labeled counts
  - 🔴 **Triggered**: Red, critical incidents needing immediate attention
  - 🟡 **Acknowledged**: Yellow, incidents being worked on
  - 🟢 **Resolved**: Green, recently resolved incidents (last 7 days)
- 📊 **Real Counts**: Uses API pagination to fetch up to 1000 incidents for accurate totals
- 📋 **Quick Lists**: 
  - Top 5 most recent active incidents (triggered + acknowledged)
  - Last 5 resolved incidents
- 🔗 **Clickable Incidents**: Click any incident to open in PagerDuty (new tab)
- 🔄 **Manual Refresh**: Button available for instant updates
- 🕐 **Timestamp**: Shows last update time (HH:MM:SS)
- 💜 **Custom Scrollbar**: Purple-themed for visual consistency
- 🎨 **Color-coded Borders**: 
  - Red left border for triggered
  - Yellow for acknowledged
  - Green for resolved

### PagerDuty Incidents (Detailed Tool)
The PagerDuty incidents tool displays:

- ✅ **Incident Status** (Triggered, Acknowledged, Resolved)
- 🔢 **Incident Number** with clickable link to PagerDuty
- 📝 **Incident Title**
- 🔧 **Service Name**
- ⚡ **Urgency Level** (High/Low)
- 📅 **Creation Time**
- 👤 **Assigned Person**
- 📊 **Summary Statistics** by status
- 🎨 **Color-coded rows** based on incident status

### PagerDuty Dashboards
The PagerDuty dashboards integration provides:

- 📈 **Overview Metrics Card** with gradient design
  - Total incidents count
  - Triggered incidents (🔴)
  - Acknowledged incidents (🟡)
  - Resolved incidents (🟢)
- 📊 **Interactive Charts** (using Chart.js)
  - Incidents by Status (Donut Chart)
  - Incidents by Urgency (Donut Chart)
  - Top 10 Services by Incident Count (Horizontal Bar Chart)
- ⏰ **Time Range**: Last 30 days of data
- 💫 **Real-time Data**: Direct from PagerDuty API

### PagerDuty Insights
The PagerDuty insights integration provides:

- 🎯 **Key Insights Card** with purple/pink gradient
  - Total incidents count
  - Average resolution time (minutes)
  - Busiest day of week
  - Busiest hour of day
- 📊 **Pattern Analysis Charts** (using Chart.js)
  - Incidents by Day of Week (Bar Chart)
  - Incidents by Hour of Day (Line Chart with area fill)
- 👥 **Top 5 Users Table**
  - Ranked by incident assignments
  - Medal system (🥇🥈🥉) for top 3
- ⏱️ **Resolution Time Metrics**
  - P50 (Median) resolution time
  - P90 resolution time
  - P95 resolution time
- 🎯 **Top Service Highlight**
  - Service with most incidents
  - Visual callout box
- ⏰ **Time Range**: Last 30 days of data
- 💫 **Advanced Analytics**: Trend identification and pattern analysis

## Troubleshooting

### Error: "PAGERDUTY_API_TOKEN not set"
- Check that `.env` file contains the token
- Verify there are no extra spaces or quotes around the token
- Restart the application after adding the token

### Error: "PagerDuty API Error 401: Unauthorized"
- Token is invalid or expired
- Generate a new token from PagerDuty
- Update `.env` file with new token

### Error: "PagerDuty API Error 403: Forbidden"
- Token doesn't have sufficient permissions
- Contact your PagerDuty admin to grant API access

### No Incidents Found
- Check that you have active incidents in PagerDuty
- Verify your search filter isn't too restrictive
- The tool only shows incidents from the last 7 days

## API Rate Limits

PagerDuty has API rate limits:
- **REST API**: 960 requests per minute per API key
- This integration makes 1 request per search

## Security Best Practices

1. ✅ Never commit `.env` file to git
2. ✅ Use read-only API tokens when possible
3. ✅ Rotate tokens periodically
4. ✅ Store tokens in secure environment variable management
5. ✅ Don't share tokens in chat, email, or documentation

## Additional Resources

- [PagerDuty REST API Documentation](https://developer.pagerduty.com/api-reference/)
- [PagerDuty API Rate Limits](https://developer.pagerduty.com/docs/ZG9jOjExMDI5NTUw-rate-limits)
- [Creating API Tokens](https://support.pagerduty.com/docs/api-access-keys)

## Support

For issues or questions:
- Check application logs: `tail -f agent_tool_logs.log`
- Contact your team administrator
- Review PagerDuty API documentation

---

**Last Updated**: February 11, 2026  
**Integration Version**: 1.0.0
