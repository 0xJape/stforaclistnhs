Maps we will be using
https://github.com/faeldon/philippines-json-maps 

For Charting:
https://plotly.com/javascript/maplibre-migration/

For weather api:
https://open-meteo.com/

Backend: Node.js
Frontend: react

Automation:
Make.com

Based on your title, ORACLIS shouldn't feel like a hospital management system—it should feel like something used inside a Disease Surveillance and Response Center or a GIS laboratory.
Imagine opening the application and immediately seeing this:
A full-screen Region XII map (rendered from GeoJSON)
Animated hotspots showing how dengue risk changes over time
A timeline slider for historical and predicted outbreaks
AI-generated insights appearing alongside the map
Minimal navigation and almost no forms
The system should answer questions like:
Where are outbreaks likely to occur next?
Why is this municipality becoming high risk?
Which neighboring municipalities are being affected?
What intervention should be prioritized?
Those are intelligence questions, not CRUD questions.
I would simplify the application to just three pages
1. Spatial Intelligence (Main Page)
This is where users spend most of their time.
Features:
Full-screen GeoJSON map of Region XII
Municipality boundaries
Risk heatmap
Temporal timeline slider
Historical vs predicted risk
Municipality information panel
Neighbor influence visualization
AI-generated hotspot explanations
This page alone should feel like 70% of the project.

2. AI Intelligence
Instead of standard dashboards, show scientific visualizations such as:
Time-series forecasting
Scatter plots
Correlation matrices
Feature importance charts
Prediction confidence intervals
Model performance metrics
AI-generated explanations for forecasts
Think of it as a research analytics dashboard.

3. Data & Reports
Keep this page lightweight:
Historical dataset viewer
Filters by municipality/date
Export reports
Import updated datasets (admin only)
Model information
No unnecessary CRUD forms.

Make.com Integration
I actually like this idea because it demonstrates that ORACLIS doesn't just predict—it also acts.
For example:
Prediction Engine

↓

Koronadal Risk = 92%

↓

Threshold exceeded

↓

Trigger Webhook

↓

Make.com

↓

Send Notifications
Make.com can automate multiple actions at once:
📧 Email the Municipal Health Office
📱 SMS to designated health officials
💬 Send a message to a Microsoft Teams or Slack channel
📢 Post an alert to a Discord server for monitoring
📄 Log the event in Google Sheets or Airtable
📋 Create a task in Asana, ClickUp, or Trello for response teams
This makes ORACLIS an early warning and response platform, not just a visualization tool.

A Possible Workflow
Historical Data
        +
Weather Data
        +
GeoJSON Boundaries
        +
Machine Learning Model
        +
Mathematical Model
        ↓
Predict Municipality Risk
        ↓
Update GIS Heatmap
        ↓
If Risk > Threshold
        ↓
Trigger Make.com Webhook
        ↓
Notify Stakeholders Automatically

Why This Will Stand Out
Most capstone projects stop at:
Recording dengue cases
Displaying charts
Showing reports
ORACLIS can go much further by combining:
Geospatial intelligence through GeoJSON-based mapping
Spatiotemporal analysis with historical and forecast timelines
AI forecasting using machine learning
Mathematical epidemiological modeling
Automated decision support with Make.com notifications
That combination makes it feel closer to a professional disease surveillance platform than a typical academic information system. It also aligns very well with your title and gives your panel a clear narrative: ORACLIS helps health authorities see where dengue risk is emerging, understand why it's happening, and automatically notify the right people when intervention may be needed.

