# Grafana Alerting Setup for CEA Sensors

This guide explains how to set up email and push notifications for sensor threshold alerts in Grafana.

## Overview

Grafana provides built-in alerting capabilities that can:
- Monitor sensor values in real-time
- Send email notifications when thresholds are exceeded
- Send push notifications via webhooks (Slack, Discord, Telegram, etc.)
- Support multiple notification channels per alert

## Prerequisites

- Grafana installed and running (see main README.md)
- PostgreSQL data source configured (see main README.md)
- SMTP server access (for email alerts) or webhook URL (for push notifications)

---

## Step 1: Configure Notification Channels

### Email Notification Channel (UI Method - Recommended)

**Location in Grafana UI (Grafana 8+ / 12.x):**

1. Open Grafana: `http://localhost:3000`
2. Click the **Alerting** icon (bell icon) in the left sidebar
3. Navigate to: **Alerting** → **Contact points** → **New contact point**
4. Configure the email channel:
   - **Name**: `CEA Email Alerts`
   - **Integration**: Select `Email` from the dropdown
   - **Email addresses**: Enter recipient email(s), one per line (e.g., `antoine.olivier.dion@gmail.com`)
   
5. **SMTP Settings** (click to expand):
   - **Host**: `smtp.gmail.com:587` (for Gmail) or your SMTP server
   - **User**: Your email address (e.g., `antoine.olivier.dion@gmail.com`)
   - **Password**: Your email password or **App Password** (required for Gmail)
   - **From address**: Your email address
   - **Skip TLS verify**: Leave **unchecked** (use TLS/STARTTLS)
   
6. Click **Test** to verify the connection (sends a test email)
7. Click **Save contact point**

**Note for Gmail users**: You must use an [App Password](https://support.google.com/accounts/answer/185833) instead of your regular password. Enable 2-factor authentication first, then generate an App Password.

**For Grafana 7.x users**: Navigate to **Alerting** → **Notification channels** → **Add channel** instead.

### Global SMTP Configuration (Recommended Method)

**This method configures SMTP globally in `grafana.ini` - affects all email notifications.**

1. **Edit Grafana configuration file:**
   ```bash
   sudo nano /etc/grafana/grafana.ini
   ```

2. **Find the `[smtp]` section** (around line 450-470) and uncomment/modify these lines:
   ```ini
   [smtp]
   enabled = true
   host = smtp.gmail.com:587
   user = antoine.olivier.dion@gmail.com
   password = your_app_password_here
   skip_verify = false
   from_address = antoine.olivier.dion@gmail.com
   from_name = CEA Alerts
   ```

   **Important notes:**
   - Remove the `;` at the start of each line to uncomment it
   - For Gmail, use an **App Password** (not your regular password)
   - If password contains `#` or `;`, wrap it with triple quotes: `"""password#here"""`

3. **Save the file** (Ctrl+O, Enter, Ctrl+X in nano)

4. **Restart Grafana:**
   ```bash
   sudo systemctl restart grafana-server
   ```

5. **Verify it's working:**
   ```bash
   sudo systemctl status grafana-server
   ```

6. **When creating contact points in Grafana UI:**
   - You can now leave SMTP settings empty (uses global config)
   - Or override them per contact point if needed

**Example configuration for Gmail:**
```ini
[smtp]
enabled = true
host = smtp.gmail.com:587
user = antoine.olivier.dion@gmail.com
password = xxxx xxxx xxxx xxxx
skip_verify = false
from_address = antoine.olivier.dion@gmail.com
from_name = CEA Alerts
```

**To get Gmail App Password:**
1. Go to https://myaccount.google.com/apppasswords
2. Enable 2-factor authentication if not already enabled
3. Generate an App Password for "Mail"
4. Use that 16-character password (spaces are fine)

### Push Notification Channel (Optional)

For push notifications via webhook (Slack, Discord, Telegram, etc.):

1. Go to **Alerting** → **Contact points** → **New contact point** (Grafana 8+)
   - Or **Alerting** → **Notification channels** → **Add channel** (Grafana 7.x)
2. Configure:
   - **Name**: `CEA Push Alerts`
   - **Integration**: `Webhook` or your service (Slack, Discord, etc.)
   - **URL**: Your webhook URL
   - **HTTP Method**: `POST`
   - **HTTP Headers**: (if required by your service)
3. Click **Test** to verify
4. Click **Save contact point** (or **Save** in Grafana 7.x)

---

## Step 2: Import Alert Rules

### Method 1: Import Dashboard with Alerts (Recommended)

The JSON files in `alert-rules/` are dashboard JSON files that include alert rules. Import them as dashboards:

1. **Open Grafana**: `http://localhost:3000`
2. **Go to Dashboards**: Click **Dashboards** → **Import** (or click **+** → **Import**)
3. **Upload JSON file**:
   - Click **Upload JSON file**
   - Navigate to: `Infrastructure/frontend/grafana/alerting/alert-rules/`
   - Select one of:
     - `temperature_alerts.json`
     - `humidity_alerts.json`
     - `water_level_alerts.json`
4. **Configure import**:
   - **Name**: Keep default or rename (e.g., "CEA Temperature Alerts")
   - **Folder**: (optional) Create or select a folder
   - **Data source**: Select **CEA Sensors** (your PostgreSQL data source)
5. **After import**:
   - Go to **Alerting** → **Alert rules**
   - Find the imported alert rules
   - Edit each rule to:
     - Select your **Contact point** (e.g., "CEA Email Alerts")
     - Review and adjust thresholds if needed
     - Click **Save rule**
6. **Repeat** for the other JSON files

### Method 2: Create Alert Rules for Your Existing Dashboard (Grafana 12)

**In Grafana 12, alerts are created as standalone alert rules (not in panel tabs).**

1. **Go to Alerting**: Click the **Alerting** icon (bell) in the left sidebar
2. **Create new rule**: Click **Alert rules** → **New alert rule**
3. **Configure the alert**:
   
   **Step 1: Set rule name**
   - **Name**: e.g., "Dry Bulb Temperature High - Back"
   - **Folder**: (optional) Select a folder for organization
   
   **Step 2: Set query**
   - **Data source**: Select **CEA Sensors** (your PostgreSQL data source)
   - **Query type**: **SQL** (or use the query builder)
   - **SQL query**: Use the same query from your dashboard panel:
     ```sql
     SELECT time, value 
     FROM measurement_with_metadata 
     WHERE sensor_name = 'dry_bulb_b' 
     AND time > $__timeFrom() 
     AND time < $__timeTo() 
     ORDER BY time DESC 
     LIMIT 1
     ```
   - **Format**: **Table** or **Time series**
   - Click **Run query** to verify it works
   
   **Step 3: Set condition**
   - **When**: `last()` (or `avg()`, `max()`, etc.)
   - **Of**: Select your metric/field (e.g., `value` or `value_0`)
   - **Is above**: `35` (your threshold)
   - Or use: **Is below**, **Is equal to**, etc.
   
   **Multiple thresholds in one rule:**
   - Click **Add query** or **Add condition** to add more conditions
   - Set first condition: `value` **Is above** `35`
   - Set second condition: `value` **Is below** `10`
   - Set operator: **OR** (alert if either condition is true)
   - This creates one alert rule that triggers for both high and low thresholds
   
   **Step 4: Set evaluation**
   - **Evaluate every**: `30s` (how often to check)
   - **For**: `1m` (duration before alerting - prevents false alarms)
   
   **Step 5: Set notifications**
   - **Contact point**: Select **CEA Email Alerts** (or create new)
   - **Message**: (optional) Customize alert message
   
4. **Save**: Click **Save rule** (top right)

**To match your dashboard panels:**
- Copy the SQL query from your dashboard panel
- Use the same sensor names and filters
- Set appropriate thresholds

**Example for temperature panel:**
- Query: Same as your `dry_bulb_b` panel query
- Condition: `last()` of `value` is above `35`
- Contact point: `CEA Email Alerts`
- Result: Email sent when temperature exceeds 35°C

### Method 3: Create Standalone Alert Rules (Alternative)

If you prefer to create alert rules separately (not tied to dashboard panels):

1. **Go to**: **Alerting** → **Alert rules** → **New alert rule**
2. **Configure**:
   - **Name**: e.g., "Dry Bulb Temperature High - Back"
   - **Folder**: (optional)
   - **Evaluation group**: (optional)
3. **Query**:
   - **Data source**: Select **CEA Sensors**
   - **Query type**: **SQL**
   - **SQL query**:
     ```sql
     SELECT time, value 
     FROM measurement_with_metadata 
     WHERE sensor_name = 'dry_bulb_b' 
     AND time > $__timeFrom() 
     AND time < $__timeTo() 
     ORDER BY time DESC 
     LIMIT 1
     ```
   - **Format**: **Table** or **Time series**
4. **Condition**:
   - **When**: `last()`
   - **Of**: `value`
   - **Is above**: `35` (or your threshold)
5. **Evaluation**:
   - **Evaluate every**: `30s`
   - **For**: `1m`
6. **Notifications**:
   - **Contact point**: Select **CEA Email Alerts**
7. **Click**: **Save rule**

### Method 3: Import via API (Advanced)

You can also import alert rules via Grafana API. See the JSON files in `alert-rules/` for reference structure.

---

## Step 3: Configure Alert Rules

### Temperature Alerts

**Dry Bulb Temperature**:
- **Alert when**: `dry_bulb_b` or `dry_bulb_f` > 35°C OR < 10°C
- **Evaluation interval**: Every 30 seconds
- **For**: 1 minute (to prevent false alarms)

**Wet Bulb Temperature**:
- **Alert when**: `wet_bulb_b` or `wet_bulb_f` > 25°C OR < 10°C

**Secondary Temperature**:
- **Alert when**: `secondary_temp_b` or `secondary_temp_f` > 35°C OR < 10°C

### Humidity Alerts

**Calculated RH** (from PT100 dry/wet bulb):
- **Alert when**: `rh_b` or `rh_f` > 90% OR < 10%

**Example Calculated RH Alert Rule Setup:**

1. **Go to**: Alerting → Alert rules → New alert rule
2. **Name**: "RH High/Low - Back" (calculated)
3. **Query**:
   ```sql
   SELECT time, value 
   FROM measurement_with_metadata 
   WHERE sensor_name = 'rh_b'
   AND time > $__timeFrom() 
   AND time < $__timeTo()
   ORDER BY time DESC 
   LIMIT 1
   ```
4. **Conditions** (add both):
   - **Condition 1**: `last()` of `value` **Is above** `90` (too high)
   - **Condition 2**: `last()` of `value` **Is below** `10` (too low)
   - **Operator**: **OR** (alert if either condition is true)
5. **Evaluation**: Every `30s`, for `1m`
6. **Notifications**: Select your contact point
7. **Save rule**

**Repeat for front location:**
- Create another rule for `rh_f` (calculated RH - front)

**Secondary RH** (from SCD30 sensor):
- **Alert when**: `secondary_rh_b` or `secondary_rh_f` > 90% OR < 10%
- Use the same setup but with `sensor_name = 'secondary_rh_b'` or `'secondary_rh_f'`

### Water Level Alerts

**Water Level** (distance from sensor to water surface):
- **Alert when**: `water_level_*` > 200mm (tank getting empty)

---

## Step 4: Test Alerts

1. Go to **Alerting** → **Alert rules**
2. Find an alert rule (e.g., "Dry Bulb Temperature High")
3. Click **Test rule** or manually trigger a test alert
4. Verify you receive the notification

---

## Alert Rule Structure

Each alert rule includes:
- **Query**: SQL query to fetch sensor data
- **Condition**: Threshold comparison (e.g., `value > 35`)
- **Evaluation**: How often to check (e.g., every 30s)
- **For**: Duration before alerting (e.g., 1 minute)
- **Notifications**: Which channels to notify

---

## Example Alert Rule (Temperature)

```json
{
  "alert": {
    "name": "Dry Bulb Temperature High - Back",
    "message": "Dry bulb temperature exceeded maximum threshold",
    "conditions": [
      {
        "query": {
          "queryType": "",
          "refId": "A",
          "datasource": {
            "type": "postgres",
            "uid": "CEA_Sensors"
          },
          "rawSql": "SELECT time, value FROM measurement_with_metadata WHERE sensor_name = 'dry_bulb_b' AND time > $__timeFrom() AND time < $__timeTo() ORDER BY time DESC LIMIT 1",
          "format": "table"
        },
        "reducer": {
          "type": "last",
          "params": []
        },
        "evaluator": {
          "params": [35],
          "type": "gt"
        },
        "operator": {
          "type": "and"
        }
      }
    ],
    "executionErrorState": "alerting",
    "for": "1m",
    "frequency": "30s",
    "notifications": ["CEA Email Alerts"]
  }
}
```

---

## Threshold Reference

Based on the previous alarm system configuration:

### Temperature Thresholds
- **Dry Bulb**: Min 10°C, Max 35°C
- **Wet Bulb**: Min 10°C, Max 25°C
- **Secondary Temp**: Min 10°C, Max 35°C
- **Lab Temp**: Min 5°C, Max 40°C
- **Water Temp**: Min 10°C, Max 30°C

### Humidity Thresholds
- **Secondary RH**: Min 10%, Max 90%
- **Lab RH**: Min 10%, Max 90%

### Water Level Thresholds
- **All water level sensors**: Max 200mm (distance from sensor to water surface)

---

## Troubleshooting

### Alerts not triggering

1. **Check alert rule state**: Go to **Alerting** → **Alert rules** → Check if rule is "Active"
2. **Verify data source**: Ensure data source is working and has recent data
3. **Check evaluation interval**: Make sure evaluation is set to a reasonable interval (30s-1m)
4. **Verify query**: Test the SQL query manually in Grafana Explore

### Notifications not sending

1. **Test notification channel**: Go to **Alerting** → **Notification channels** → Click **Test**
2. **Check SMTP settings**: Verify SMTP host, port, credentials
3. **Check webhook URL**: For push notifications, verify webhook URL is correct
4. **Check Grafana logs**: `sudo journalctl -u grafana-server -f`

### Too many alerts

1. **Increase "For" duration**: Set "For" to 2-5 minutes to prevent false alarms
2. **Add cooldown**: Configure alert rule to only notify once per hour/day
3. **Adjust thresholds**: Review and adjust threshold values

---

## Advanced Configuration

### Multiple Notification Channels

You can send alerts to multiple channels:
1. Create multiple notification channels (Email, Slack, Discord, etc.)
2. In alert rule, select all desired channels
3. Alerts will be sent to all selected channels

### Alert Grouping

Group related alerts to reduce notification spam:
1. In alert rule, enable **Group by**
2. Group by: `sensor_name`, `location`, etc.
3. Set **Group wait**: 30s (wait before sending grouped alerts)
4. Set **Group interval**: 5m (interval between grouped alerts)

### Alert Templates

Customize alert messages with templates:
- Use `{{ $labels.sensor_name }}` for sensor name
- Use `{{ $value }}` for current value
- Use `{{ $threshold }}` for threshold value

Example template:
```
CEA Alarm: {{ $labels.sensor_name }} = {{ $value }}°C
Location: {{ $labels.location }}/{{ $labels.cluster }}
Threshold: {{ $threshold }}°C
```

---

## Next Steps

- Customize alert thresholds for your specific needs
- Add more alert rules for other sensors (CO2, VPD, Pressure)
- Set up alert grouping to reduce notification spam
- Create alert dashboards to visualize alert history
- Configure alert routing based on severity

---

## See Also

- [Grafana Alerting Documentation](https://grafana.com/docs/grafana/latest/alerting/)
- [Grafana Notification Channels](https://grafana.com/docs/grafana/latest/alerting/notifications/)
- Main Grafana README: `Infrastructure/frontend/grafana/README.md`

