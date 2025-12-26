{
  "annotations": {
    "list": [
      {
        "builtIn": 1,
        "datasource": {
          "type": "grafana",
          "uid": "-- Grafana --"
        },
        "enable": true,
        "hide": true,
        "iconColor": "rgba(0, 211, 255, 1)",
        "name": "Annotations & Alerts",
        "type": "dashboard"
      }
    ]
  },
  "description": "Flower room",
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 0,
  "id": 0,
  "links": [],
  "liveNow": true,
  "panels": [
    {
      "description": "Average values from Front and Back clusters",
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "footer": {
              "reducers": []
            },
            "inspect": false
          },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              }
            ]
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Sensor"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 100
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Value"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 100
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 9,
        "w": 4,
        "x": 0,
        "y": 0
      },
      "id": 1,
      "options": {
        "cellHeight": "sm",
        "showHeader": true,
        "sortBy": []
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "table",
          "rawSql": "WITH latest_f AS (SELECT DISTINCT ON (s.sensor_id) s.name AS sensor_name, m.value, m.time FROM measurement m JOIN sensor s ON m.sensor_id = s.sensor_id WHERE s.name LIKE '%_f' AND s.name NOT LIKE 'secondary_%' AND m.time >= NOW() - INTERVAL '10 minutes' ORDER BY s.sensor_id, m.time DESC), latest_b AS (SELECT DISTINCT ON (s.sensor_id) s.name AS sensor_name, m.value, m.time FROM measurement m JOIN sensor s ON m.sensor_id = s.sensor_id WHERE s.name LIKE '%_b' AND s.name NOT LIKE 'secondary_%' AND m.time >= NOW() - INTERVAL '10 minutes' ORDER BY s.sensor_id, m.time DESC), sensor_data AS (SELECT CASE WHEN REPLACE(f.sensor_name, '_f', '') = 'dry_bulb' THEN 'Dry Bulb Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'wet_bulb' THEN 'Wet Bulb Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'rh' THEN 'RH Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'vpd' THEN 'VPD Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'co2' THEN 'CO2 Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'pressure' THEN 'Pressure Avg' WHEN REPLACE(f.sensor_name, '_f', '') = 'water_level' THEN 'Water Level Avg' ELSE REPLACE(f.sensor_name, '_f', '') || ' Avg' END AS \"Sensor\", ROUND((f.value + b.value) / 2.0, 2) AS value_num, CASE WHEN REPLACE(f.sensor_name, '_f', '') = 'dry_bulb' THEN '°C' WHEN REPLACE(f.sensor_name, '_f', '') = 'wet_bulb' THEN '°C' WHEN REPLACE(f.sensor_name, '_f', '') = 'rh' THEN '%' WHEN REPLACE(f.sensor_name, '_f', '') = 'vpd' THEN ' kPa' WHEN REPLACE(f.sensor_name, '_f', '') = 'co2' THEN ' ppm' WHEN REPLACE(f.sensor_name, '_f', '') = 'pressure' THEN ' hPa' WHEN REPLACE(f.sensor_name, '_f', '') = 'water_level' THEN ' mm' ELSE '' END AS unit, GREATEST(f.time, b.time) AS time_val, CASE WHEN REPLACE(f.sensor_name, '_f', '') = 'dry_bulb' THEN 1 WHEN REPLACE(f.sensor_name, '_f', '') = 'wet_bulb' THEN 2 WHEN REPLACE(f.sensor_name, '_f', '') = 'rh' THEN 3 WHEN REPLACE(f.sensor_name, '_f', '') = 'vpd' THEN 4 WHEN REPLACE(f.sensor_name, '_f', '') = 'co2' THEN 5 WHEN REPLACE(f.sensor_name, '_f', '') = 'pressure' THEN 6 WHEN REPLACE(f.sensor_name, '_f', '') = 'water_level' THEN 9 ELSE 10 END AS sort_order FROM latest_f f JOIN latest_b b ON REPLACE(f.sensor_name, '_f', '') = REPLACE(b.sensor_name, '_b', '')), combined AS (SELECT \"Sensor\", (value_num::text || unit) AS \"Value\", sort_order FROM sensor_data UNION ALL SELECT 'Last Update' AS \"Sensor\", TO_CHAR(MAX(time_val), 'YYYY/MM/DD HH24:MI:SS') AS \"Value\", 999 AS sort_order FROM sensor_data) SELECT \"Sensor\", \"Value\" FROM combined ORDER BY sort_order, \"Sensor\"",
          "refId": "A"
        }
      ],
      "title": "Averages",
      "type": "table"
    },
    {
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "bf6vebq5ipybke"
      },
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "palette-classic"
          },
          "custom": {
            "axisBorderShow": true,
            "axisCenteredZero": false,
            "axisColorMode": "series",
            "axisLabel": "",
            "axisPlacement": "auto",
            "axisSoftMin": 15,
            "barAlignment": 0,
            "barWidthFactor": 0.6,
            "drawStyle": "line",
            "fillOpacity": 0,
            "gradientMode": "none",
            "hideFrom": {
              "legend": false,
              "tooltip": false,
              "viz": false
            },
            "insertNulls": false,
            "lineInterpolation": "linear",
            "lineStyle": {
              "fill": "solid"
            },
            "lineWidth": 1,
            "pointSize": 5,
            "scaleDistribution": {
              "type": "linear"
            },
            "showPoints": "auto",
            "showValues": false,
            "spanNulls": true,
            "stacking": {
              "group": "A",
              "mode": "none"
            },
            "thresholdsStyle": {
              "mode": "off"
            }
          },
          "decimals": 2,
          "fieldMinMax": true,
          "mappings": [],
          "min": 0,
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              },
              {
                "color": "red",
                "value": 80
              }
            ]
          },
          "unit": "celsius"
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "dry_bulb_f"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "Dry Bulb (°C) - Front"
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "wet_bulb_f"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "Wet Bulb (°C) - Front"
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "rh_f"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "RH (%) - Front"
              },
              {
                "id": "unit",
                "value": "percent"
              },
              {
                "id": "decimals",
                "value": 1
              },
              {
                "id": "custom.axisPlacement",
                "value": "right"
              },
              {
                "id": "custom.axisSoftMax",
                "value": 100
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "vpd_f"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "VPD (kPa) - Front"
              },
              {
                "id": "unit",
                "value": "kpa"
              },
              {
                "id": "decimals",
                "value": 2
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "dry_bulb_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "Dry Bulb (°C) - Back"
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "wet_bulb_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "Wet Bulb (°C) - Back"
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "rh_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "RH (%) - Back"
              },
              {
                "id": "unit",
                "value": "percent"
              },
              {
                "id": "decimals",
                "value": 1
              },
              {
                "id": "custom.axisPlacement",
                "value": "right"
              },
              {
                "id": "custom.axisSoftMax",
                "value": 100
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "vpd_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "VPD (kPa) - Back"
              },
              {
                "id": "unit",
                "value": "kpa"
              },
              {
                "id": "decimals",
                "value": 2
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Wet Bulb (°C) - Back"
            },
            "properties": [
              {
                "id": "color",
                "value": {
                  "fixedColor": "green",
                  "mode": "fixed"
                }
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Dry Bulb (°C) - Back"
            },
            "properties": [
              {
                "id": "color",
                "value": {
                  "fixedColor": "orange",
                  "mode": "fixed"
                }
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Temp Setpoint - Main"
            },
            "properties": [
              {
                "id": "color",
                "value": {
                  "fixedColor": "red",
                  "mode": "fixed"
                }
              },
              {
                "id": "custom.lineStyle",
                "value": {
                  "dash": [
                    0,
                    5
                  ],
                  "fill": "dot"
                }
              },
              {
                "id": "custom.lineWidth",
                "value": 2
              },
              {
                "id": "custom.lineInterpolation",
                "value": "stepBefore"
              },
              {
                "id": "custom.fillOpacity",
                "value": 0
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "VPD Setpoint - Main"
            },
            "properties": [
              {
                "id": "color",
                "value": {
                  "fixedColor": "blue",
                  "mode": "fixed"
                }
              },
              {
                "id": "custom.lineStyle",
                "value": {
                  "dash": [
                    0,
                    5
                  ],
                  "fill": "dot"
                }
              },
              {
                "id": "custom.lineWidth",
                "value": 2
              },
              {
                "id": "unit",
                "value": "kpa"
              },
              {
                "id": "custom.lineInterpolation",
                "value": "stepBefore"
              },
              {
                "id": "custom.fillOpacity",
                "value": 0
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "DAY Period Overlay"
            },
            "properties": [
              {
                "id": "color",
                "value": {
                  "fixedColor": "yellow",
                  "mode": "fixed"
                }
              },
              {
                "id": "custom.drawStyle",
                "value": "line"
              },
              {
                "id": "custom.fillOpacity",
                "value": 15
              },
              {
                "id": "custom.gradientMode",
                "value": "none"
              },
              {
                "id": "custom.lineWidth",
                "value": 0
              },
              {
                "id": "custom.axisPlacement",
                "value": "right"
              },
              {
                "id": "custom.axisSoftMax",
                "value": 100
              },
              {
                "id": "custom.axisSoftMin",
                "value": 0
              },
              {
                "id": "unit",
                "value": "percent"
              },
              {
                "id": "custom.hideFrom",
                "value": {
                  "legend": true,
                  "tooltip": true,
                  "viz": false
                }
              },
              {
                "id": "custom.axisPlacement",
                "value": "hidden"
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 24,
        "w": 20,
        "x": 4,
        "y": 0
      },
      "id": 4,
      "options": {
        "legend": {
          "calcs": [],
          "displayMode": "list",
          "placement": "bottom",
          "showLegend": true
        },
        "tooltip": {
          "hideZeros": false,
          "mode": "multi",
          "sort": "none"
        }
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "time_series",
          "rawSql": "SELECT time AS \"time\", sensor_name AS metric, value FROM measurement_with_metadata WHERE sensor_name IN ('dry_bulb_f', 'wet_bulb_f', 'rh_f', 'vpd_f', 'dry_bulb_b', 'wet_bulb_b', 'rh_b', 'vpd_b') AND time >= $__timeFrom() AND time <= $__timeTo() ORDER BY time, sensor_name",
          "refId": "A"
        },
        {
          "format": "time_series",
          "hide": false,
          "rawSql": "WITH filtered_setpoints AS (\n  SELECT \n    sh.timestamp,\n    sh.temperature\n  FROM setpoint_history sh\n  WHERE sh.location = 'Flower Room' \n    AND sh.cluster = 'main' \n    AND sh.temperature IS NOT NULL\n    AND sh.timestamp >= $__timeFrom()\n    AND sh.timestamp <= $__timeTo()\n    AND (\n      (sh.mode = 'DAY' AND EXISTS (\n        SELECT 1 FROM schedules s\n        WHERE s.location = 'Flower Room'\n          AND s.cluster = 'main'\n          AND s.mode = 'DAY'\n          AND s.enabled = true\n          AND (s.day_of_week IS NULL OR s.day_of_week = EXTRACT(DOW FROM sh.timestamp))\n          AND (\n            (s.start_time <= s.end_time AND sh.timestamp::time >= s.start_time AND sh.timestamp::time < s.end_time)\n            OR (s.start_time > s.end_time AND (sh.timestamp::time >= s.start_time OR sh.timestamp::time < s.end_time))\n          )\n      ))\n      OR\n      (sh.mode = 'NIGHT' AND NOT EXISTS (\n        SELECT 1 FROM schedules s\n        WHERE s.location = 'Flower Room'\n          AND s.cluster = 'main'\n          AND s.mode = 'DAY'\n          AND s.enabled = true\n          AND (s.day_of_week IS NULL OR s.day_of_week = EXTRACT(DOW FROM sh.timestamp))\n          AND (\n            (s.start_time <= s.end_time AND sh.timestamp::time >= s.start_time AND sh.timestamp::time < s.end_time)\n            OR (s.start_time > s.end_time AND (sh.timestamp::time >= s.start_time OR sh.timestamp::time < s.end_time))\n          )\n      ))\n    )\n),\nlast_setpoint AS (\n  SELECT temperature, timestamp\n  FROM filtered_setpoints\n  ORDER BY timestamp DESC\n  LIMIT 1\n)\nSELECT timestamp AS \"time\", 'Temp Setpoint - Main' AS metric, temperature AS value\nFROM filtered_setpoints\nUNION ALL\nSELECT $__timeTo() AS \"time\", 'Temp Setpoint - Main' AS metric, temperature AS value\nFROM last_setpoint\nWHERE temperature IS NOT NULL\nORDER BY \"time\"",
          "refId": "B"
        },
        {
          "format": "time_series",
          "hide": false,
          "rawSql": "WITH filtered_setpoints AS (\n  SELECT \n    sh.timestamp,\n    sh.vpd\n  FROM setpoint_history sh\n  WHERE sh.location = 'Flower Room' \n    AND sh.cluster = 'main' \n    AND sh.vpd IS NOT NULL\n    AND sh.timestamp >= $__timeFrom()\n    AND sh.timestamp <= $__timeTo()\n    AND (\n      (sh.mode = 'DAY' AND EXISTS (\n        SELECT 1 FROM schedules s\n        WHERE s.location = 'Flower Room'\n          AND s.cluster = 'main'\n          AND s.mode = 'DAY'\n          AND s.enabled = true\n          AND (s.day_of_week IS NULL OR s.day_of_week = EXTRACT(DOW FROM sh.timestamp))\n          AND (\n            (s.start_time <= s.end_time AND sh.timestamp::time >= s.start_time AND sh.timestamp::time < s.end_time)\n            OR (s.start_time > s.end_time AND (sh.timestamp::time >= s.start_time OR sh.timestamp::time < s.end_time))\n          )\n      ))\n      OR\n      (sh.mode = 'NIGHT' AND NOT EXISTS (\n        SELECT 1 FROM schedules s\n        WHERE s.location = 'Flower Room'\n          AND s.cluster = 'main'\n          AND s.mode = 'DAY'\n          AND s.enabled = true\n          AND (s.day_of_week IS NULL OR s.day_of_week = EXTRACT(DOW FROM sh.timestamp))\n          AND (\n            (s.start_time <= s.end_time AND sh.timestamp::time >= s.start_time AND sh.timestamp::time < s.end_time)\n            OR (s.start_time > s.end_time AND (sh.timestamp::time >= s.start_time OR sh.timestamp::time < s.end_time))\n          )\n      ))\n    )\n),\nlast_setpoint AS (\n  SELECT vpd, timestamp\n  FROM filtered_setpoints\n  ORDER BY timestamp DESC\n  LIMIT 1\n)\nSELECT timestamp AS \"time\", 'VPD Setpoint - Main' AS metric, vpd AS value\nFROM filtered_setpoints\nUNION ALL\nSELECT $__timeTo() AS \"time\", 'VPD Setpoint - Main' AS metric, vpd AS value\nFROM last_setpoint\nWHERE vpd IS NOT NULL\nORDER BY \"time\"",
          "refId": "C"
        },
        {
          "format": "time_series",
          "hide": false,
          "rawSql": "WITH time_points AS (\n  SELECT generate_series($__timeFrom()::timestamp, $__timeTo()::timestamp, INTERVAL '5 minute') AS time\n)\nSELECT \n  tp.time AS \"time\",\n  'DAY Period Overlay' AS metric,\n  CASE \n    WHEN EXISTS (\n      SELECT 1 FROM schedules \n      WHERE location = 'Flower Room' \n        AND cluster = 'main'\n        AND mode = 'DAY'\n        AND enabled = true\n        AND (day_of_week IS NULL OR day_of_week = EXTRACT(DOW FROM tp.time))\n        AND (\n          (start_time <= end_time AND tp.time::time >= start_time AND tp.time::time < end_time)\n          OR (start_time > end_time AND (tp.time::time >= start_time OR tp.time::time < end_time))\n        )\n    ) THEN 100\n    ELSE NULL\n  END AS value\nFROM time_points tp",
          "refId": "D"
        }
      ],
      "title": "Temperature, RH & VPD - Main Graph",
      "type": "timeseries"
    },
    {
      "description": "Front cluster sensors (_f)",
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "footer": {
              "reducers": []
            },
            "inspect": false
          },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              }
            ]
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Sensor"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 138
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Value"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 114
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 11,
        "w": 4,
        "x": 0,
        "y": 9
      },
      "id": 2,
      "options": {
        "cellHeight": "sm",
        "showHeader": true,
        "sortBy": []
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "table",
          "rawSql": "WITH sensor_data AS (SELECT DISTINCT ON (s.sensor_id) CASE s.name WHEN 'dry_bulb_f' THEN 'Dry Bulb' WHEN 'wet_bulb_f' THEN 'Wet Bulb' WHEN 'rh_f' THEN 'RH' WHEN 'vpd_f' THEN 'VPD' WHEN 'co2_f' THEN 'CO2' WHEN 'pressure_f' THEN 'Pressure' WHEN 'secondary_temp_f' THEN 'Secondary Temp' WHEN 'secondary_rh_f' THEN 'Secondary RH' WHEN 'water_level_f' THEN 'Water Level' ELSE s.name END AS \"Sensor\", m.value AS value_num, CASE WHEN s.name LIKE 'dry_bulb%' OR s.name LIKE 'secondary_temp%' THEN '°C' WHEN s.name LIKE 'wet_bulb%' THEN '°C' WHEN s.name LIKE 'rh%' AND s.name NOT LIKE 'secondary_rh%' THEN '%' WHEN s.name LIKE 'secondary_rh%' THEN '%' WHEN s.name LIKE 'vpd%' THEN ' kPa' WHEN s.name LIKE 'co2%' THEN ' ppm' WHEN s.name LIKE 'pressure%' THEN ' hPa' WHEN s.name LIKE 'water_level%' THEN ' mm' ELSE '' END AS unit, m.time AS time_val, CASE WHEN s.name LIKE 'dry_bulb%' THEN 1 WHEN s.name LIKE 'wet_bulb%' THEN 2 WHEN s.name LIKE 'rh%' AND s.name NOT LIKE 'secondary_rh%' THEN 3 WHEN s.name LIKE 'vpd%' THEN 4 WHEN s.name LIKE 'co2%' THEN 5 WHEN s.name LIKE 'pressure%' THEN 6 WHEN s.name LIKE 'secondary_temp%' THEN 7 WHEN s.name LIKE 'secondary_rh%' THEN 8 WHEN s.name LIKE 'water_level%' THEN 9 ELSE 10 END AS sort_order FROM measurement m JOIN sensor s ON m.sensor_id = s.sensor_id WHERE s.name LIKE '%_f' AND m.time >= NOW() - INTERVAL '10 minutes' ORDER BY s.sensor_id, m.time DESC), combined AS (SELECT \"Sensor\", (value_num::text || unit) AS \"Value\", sort_order FROM sensor_data UNION ALL SELECT 'Last Update' AS \"Sensor\", TO_CHAR(MAX(time_val), 'YYYY/MM/DD HH24:MI:SS') AS \"Value\", 999 AS sort_order FROM sensor_data) SELECT \"Sensor\", \"Value\" FROM combined ORDER BY sort_order, \"Sensor\"",
          "refId": "A"
        }
      ],
      "title": "Front Cluster",
      "type": "table"
    },
    {
      "description": "Back cluster sensors (_b)",
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "footer": {
              "reducers": []
            },
            "inspect": false
          },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              }
            ]
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Sensor"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 129
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Value"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 148
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 11,
        "w": 4,
        "x": 0,
        "y": 20
      },
      "id": 3,
      "options": {
        "cellHeight": "sm",
        "showHeader": true,
        "sortBy": []
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "table",
          "rawSql": "WITH sensor_data AS (SELECT DISTINCT ON (s.sensor_id) CASE s.name WHEN 'dry_bulb_b' THEN 'Dry Bulb' WHEN 'wet_bulb_b' THEN 'Wet Bulb' WHEN 'rh_b' THEN 'RH' WHEN 'vpd_b' THEN 'VPD' WHEN 'co2_b' THEN 'CO2' WHEN 'pressure_b' THEN 'Pressure' WHEN 'secondary_temp_b' THEN 'Secondary Temp' WHEN 'secondary_rh_b' THEN 'Secondary RH' WHEN 'water_level_b' THEN 'Water Level' ELSE s.name END AS \"Sensor\", m.value AS value_num, CASE WHEN s.name LIKE 'dry_bulb%' OR s.name LIKE 'secondary_temp%' THEN '°C' WHEN s.name LIKE 'wet_bulb%' THEN '°C' WHEN s.name LIKE 'rh%' AND s.name NOT LIKE 'secondary_rh%' THEN '%' WHEN s.name LIKE 'secondary_rh%' THEN '%' WHEN s.name LIKE 'vpd%' THEN ' kPa' WHEN s.name LIKE 'co2%' THEN ' ppm' WHEN s.name LIKE 'pressure%' THEN ' hPa' WHEN s.name LIKE 'water_level%' THEN ' mm' ELSE '' END AS unit, m.time AS time_val, CASE WHEN s.name LIKE 'dry_bulb%' THEN 1 WHEN s.name LIKE 'wet_bulb%' THEN 2 WHEN s.name LIKE 'rh%' AND s.name NOT LIKE 'secondary_rh%' THEN 3 WHEN s.name LIKE 'vpd%' THEN 4 WHEN s.name LIKE 'co2%' THEN 5 WHEN s.name LIKE 'pressure%' THEN 6 WHEN s.name LIKE 'secondary_temp%' THEN 7 WHEN s.name LIKE 'secondary_rh%' THEN 8 WHEN s.name LIKE 'water_level%' THEN 9 ELSE 10 END AS sort_order FROM measurement m JOIN sensor s ON m.sensor_id = s.sensor_id WHERE s.name LIKE '%_b' AND m.time >= NOW() - INTERVAL '10 minutes' ORDER BY s.sensor_id, m.time DESC), combined AS (SELECT \"Sensor\", (value_num::text || unit) AS \"Value\", sort_order FROM sensor_data UNION ALL SELECT 'Last Update' AS \"Sensor\", TO_CHAR(MAX(time_val), 'YYYY/MM/DD HH24:MI:SS') AS \"Value\", 999 AS sort_order FROM sensor_data) SELECT \"Sensor\", \"Value\" FROM combined ORDER BY sort_order, \"Sensor\"",
          "refId": "A"
        }
      ],
      "title": "Back Cluster",
      "type": "table"
    },
    {
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "bf6vebq5ipybke"
      },
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "palette-classic"
          },
          "custom": {
            "axisBorderShow": false,
            "axisCenteredZero": false,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "barWidthFactor": 0.6,
            "drawStyle": "line",
            "fillOpacity": 0,
            "gradientMode": "none",
            "hideFrom": {
              "legend": false,
              "tooltip": false,
              "viz": false
            },
            "insertNulls": false,
            "lineInterpolation": "linear",
            "lineWidth": 1,
            "pointSize": 5,
            "scaleDistribution": {
              "type": "linear"
            },
            "showPoints": "never",
            "showValues": false,
            "spanNulls": true,
            "stacking": {
              "group": "A",
              "mode": "none"
            },
            "thresholdsStyle": {
              "mode": "off"
            }
          },
          "decimals": 0,
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              },
              {
                "color": "red",
                "value": 80
              }
            ]
          },
          "unit": "ppm"
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "pressure_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "Pressure (hPa) - Back"
              },
              {
                "id": "unit",
                "value": "hpa"
              },
              {
                "id": "decimals",
                "value": 1
              },
              {
                "id": "custom.axisPlacement",
                "value": "right"
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "co2_b"
            },
            "properties": [
              {
                "id": "displayName",
                "value": "CO₂ - Back"
              },
              {
                "id": "unit",
                "value": "ppm"
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 8,
        "w": 20,
        "x": 4,
        "y": 24
      },
      "id": 5,
      "options": {
        "legend": {
          "calcs": [],
          "displayMode": "list",
          "placement": "bottom",
          "showLegend": true
        },
        "tooltip": {
          "hideZeros": false,
          "mode": "multi",
          "sort": "none"
        }
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "time_series",
          "rawSql": "SELECT time AS \"time\", sensor_name AS metric, value FROM measurement_with_metadata WHERE (sensor_name LIKE 'co2_%' OR sensor_name = 'pressure_b') AND time >= $__timeFrom() AND time <= $__timeTo() ORDER BY time, sensor_name",
          "refId": "A"
        }
      ],
      "title": "CO2 & Pressure",
      "type": "timeseries"
    },
    {
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "footer": {
              "reducers": []
            },
            "inspect": false
          },
          "decimals": 2,
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              {
                "color": "green",
                "value": 0
              },
              {
                "color": "red",
                "value": 80
              }
            ]
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Sensor"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 200
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Min"
            },
            "properties": [
              {
                "id": "decimals",
                "value": 2
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Max"
            },
            "properties": [
              {
                "id": "decimals",
                "value": 2
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Average"
            },
            "properties": [
              {
                "id": "decimals",
                "value": 2
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Std Dev"
            },
            "properties": [
              {
                "id": "decimals",
                "value": 2
              }
            ]
          }
        ]
      },
      "gridPos": {
        "h": 8,
        "w": 21,
        "x": 3,
        "y": 32
      },
      "id": 6,
      "options": {
        "cellHeight": "sm",
        "showHeader": true,
        "sortBy": []
      },
      "pluginVersion": "12.3.1",
      "targets": [
        {
          "format": "table",
          "rawSql": "SELECT CASE sensor_name WHEN 'dry_bulb_f' THEN 'Dry Bulb (C) - Front' WHEN 'wet_bulb_f' THEN 'Wet Bulb (C) - Front' WHEN 'rh_f' THEN 'RH (%) - Front' WHEN 'vpd_f' THEN 'VPD (kPa) - Front' WHEN 'dry_bulb_b' THEN 'Dry Bulb (C) - Back' WHEN 'wet_bulb_b' THEN 'Wet Bulb (C) - Back' WHEN 'rh_b' THEN 'RH (%) - Back' WHEN 'vpd_b' THEN 'VPD (kPa) - Back' ELSE sensor_name END AS \\\"Sensor\\\", MIN(value) AS \\\"Min\\\", MAX(value) AS \\\"Max\\\", AVG(value) AS \\\"Average\\\", STDDEV(value) AS \\\"Std Dev\\\" FROM measurement_with_metadata WHERE sensor_name IN ('dry_bulb_f', 'wet_bulb_f', 'rh_f', 'vpd_f', 'dry_bulb_b', 'wet_bulb_b', 'rh_b', 'vpd_b') AND time >= $__timeFrom() AND time <= $__timeTo() GROUP BY sensor_name ORDER BY CASE WHEN sensor_name LIKE 'dry_bulb%' THEN 1 WHEN sensor_name LIKE 'wet_bulb%' THEN 2 WHEN sensor_name LIKE 'rh%' AND sensor_name NOT LIKE 'secondary_rh%' THEN 3 WHEN sensor_name LIKE 'vpd%' THEN 4 ELSE 10 END, sensor_name",
          "refId": "A"
        }
      ],
      "title": "Statistics - Main Graph Sensors",
      "type": "table"
    }
  ],
  "preload": false,
  "refresh": "1s",
  "schemaVersion": 42,
  "tags": [
    "cea",
    "sensors",
    "timescaledb"
  ],
  "templating": {
    "list": []
  },
  "time": {
    "from": "now-30m",
    "to": "now"
  },
  "timepicker": {
    "refresh_intervals": [
      "1s",
      "5s",
      "10s",
      "30s",
      "1m",
      "5m",
      "15m",
      "30m",
      "1h",
      "2h",
      "1d"
    ]
  },
  "timezone": "browser",
  "title": "Siberian Jungle : Flower Sector",
  "uid": "7467103e-9964-4e06-9fc8-c43610129ba9",
  "version": 17
}