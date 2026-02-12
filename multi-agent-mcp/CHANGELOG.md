# 📝 Changelog - GOC_AgenticAI

All notable changes to this project will be documented in this file.

## [2.0.0] - 2026-02-09

### 🎉 Major Changes

#### Rebranding
- **BREAKING**: Renamed application from `Arlo_AgenticAI` to `GOC_AgenticAI`
- Updated all documentation, UI elements, and Docker configurations
- Centered application title in main header for better prominence

### 🆕 New Features

#### PagerDuty Integration Suite
- **PagerDuty Status Monitor** (Auto-Refresh)
  - Real-time incident tracking card in main content area
  - Located next to "How to use" section for prominence
  - Auto-refreshes every 3 minutes
  - Visual traffic light display with labeled counts (Triggered, Acknowledged, Resolved)
  - Displays top 5 active and last 5 resolved incidents
  - Clickable incidents open directly in PagerDuty
  - Full API pagination fetches up to 1000 incidents for accurate counts
  - Manual refresh button available
  - Custom purple-themed scrollbar

- **PagerDuty Tool** (Detailed List)
  - Comprehensive incidents table with all details
  - Last 7 days of incidents
  - Filter by service name or incident number
  - Color-coded rows by status (red/yellow/green)
  - Clickable incident numbers link to PagerDuty
  - Shows status, urgency, creation time, and assignments

- **PagerDuty_Dashboards Tool** (Analytics)
  - Interactive analytics dashboard with Chart.js visualizations
  - Overview metrics card (gradient design)
  - Three interactive charts:
    - Incidents by Status (Donut Chart)
    - Incidents by Urgency (Donut Chart)
    - Top 10 Services (Horizontal Bar Chart)
  - Last 30 days of data

- **PagerDuty_Insights Tool** (Advanced Analytics)
  - Key metrics: Total incidents, avg resolution time, busiest day/hour
  - Pattern analysis charts:
    - Incidents by Day of Week (Bar Chart)
    - Incidents by Hour of Day (Line Chart with area fill)
  - Top 5 users ranking with medal system (🥇🥈🥉)
  - Resolution time percentiles (P50, P90, P95)
  - Top service highlight
  - Last 30 days of data

### 🎨 UI/UX Enhancements

#### Layout Improvements
- **Centered Header**: Application title now centered for professional appearance
- **Theme Toggle**: Repositioned to absolute right using CSS positioning
- **Two-Column Main Area**: "How to use" section paired with PagerDuty Status card
- **Optimized Sidebar**: Arlo Status reverted to single-column layout
  - All Core Services visible without scrolling
  - Past Incidents section remains scrollable (max 7)

#### History Section Redesign
- **Compact by Default**: Shows only last 3 searches
- **Smart Expansion**: "Show X more" button reveals all history
- **Collapsible**: "Show less" button to return to compact view
- **Search Override**: When searching history, shows all matching results (ignores 3-item limit)
- **Auto-Reset**: Collapses automatically when search is cleared

#### Visual Consistency
- **Unified Colors**: All section titles use consistent teal/green (#20d6ca)
- **PagerDuty Card Styling**: 
  - Purple/pink gradient summary card
  - Custom purple scrollbar for incident lists
  - Color-coded incident borders (red, yellow, green)
  - Hover effects for all interactive elements
  - Smooth transitions and visual feedback

### 🔧 Technical Improvements

#### API Enhancements
- **PagerDuty Pagination**: Implemented full pagination in `/api/pagerduty/monitor` endpoint
  - Fetches all incidents (up to 1000 safety limit)
  - Provides accurate counts instead of limiting to 100
  - Handles API rate limiting gracefully
- **Increased Timeout**: PagerDuty API requests timeout increased to 15 seconds
- **Error Handling**: Better error messages and graceful degradation

#### Code Organization
- Created modular PagerDuty tools:
  - `tools/pagerduty_tool.py` - Incidents list
  - `tools/pagerduty_analytics.py` - Analytics dashboard
  - `tools/pagerduty_insights.py` - Insights & trends
- Updated `app.py` to register all three PagerDuty tools
- Added `html_url` to incident data for clickable links

#### Configuration
- Added `PAGERDUTY_API_TOKEN` to environment variables
- Updated `.env.example` with PagerDuty configuration
- Created comprehensive `PAGERDUTY_SETUP.md` documentation

### 📚 Documentation Updates

#### New Documentation
- **PAGERDUTY_SETUP.md**: Comprehensive PagerDuty integration setup guide
- **CHANGELOG.md**: This file, tracking all version changes

#### Updated Documentation
- **README.md**: 
  - Added "What's New in v2.0" section
  - Updated Key Features with PagerDuty details
  - Added PagerDuty Status Monitor documentation
  - Updated UI/UX Features section
  - Added Interface Layout diagram
  - Updated Tool Names Reference with categorization
  - Added PagerDuty usage examples
  - Updated acknowledgments

- **QUICK_START.md**:
  - Updated test instructions with PagerDuty
  - Added History expansion instructions
  - Updated interaction examples

- **DOCKER_README.md**:
  - Added "New Features in v2.0" section
  - Updated environment variables list
  - Added PagerDuty configuration details

- **DATADOG_SETUP.md**:
  - Updated branding to GOC_AgenticAI

### 🐛 Bug Fixes
- Fixed Spanish console.error messages in `scripts.js` (changed to English for consistency)
- Fixed History dropdown state management
- Fixed theme toggle positioning with centered header
- Corrected Core Services display to show all items without dropdown

### ⚡ Performance Improvements
- JavaScript history rendering optimized for large history lists
- PagerDuty pagination reduces unnecessary API calls
- Better caching strategy for auto-refresh monitors

### 🔒 Security
- Added timeout protection for PagerDuty API calls
- Implemented safety limits for pagination (1000 max)
- Improved error handling to prevent information leakage

---

## [1.0.0] - 2025-12-XX

### Initial Release
- Multi-agent chatbot system
- Datadog integration with RED metrics
- Confluence documentation search
- Service ownership lookup
- Arlo Status monitor
- LLaMA 3 and Gemini AI integration
- Splunk P0 Streaming integration
- On-call support tool
- Dark theme UI
- Search history
- Docker support

---

## Versioning

This project follows [Semantic Versioning](https://semver.org/):
- **MAJOR** version: Incompatible API changes
- **MINOR** version: New functionality (backwards compatible)
- **PATCH** version: Bug fixes (backwards compatible)

## Legend
- 🎉 Major Changes
- 🆕 New Features
- 🎨 UI/UX
- 🔧 Technical
- 📚 Documentation
- 🐛 Bug Fixes
- ⚡ Performance
- 🔒 Security
- ⚠️ Breaking Changes
- 🗑️ Deprecations
