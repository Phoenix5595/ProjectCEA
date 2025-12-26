-- Diagnostic queries to check setpoints table
-- Run these in Grafana's Query Inspector or directly in PostgreSQL

-- 1. Check if setpoints table exists and its structure
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'setpoints'
ORDER BY ordinal_position;

-- 2. Check what data exists in setpoints table
SELECT * FROM setpoints 
ORDER BY updated_at DESC 
LIMIT 10;

-- 3. Check specific location/cluster combinations
SELECT location, cluster, mode, temperature, vpd, updated_at 
FROM setpoints 
WHERE location LIKE '%Flower%' OR location LIKE '%flower%'
ORDER BY updated_at DESC;

-- 4. Test query for temperature setpoint (simplified)
SELECT 
  generate_series(NOW() - INTERVAL '1 hour', NOW(), INTERVAL '10 minute') AS "time",
  'Temp Setpoint - Back' AS metric,
  (SELECT temperature FROM setpoints 
   WHERE location = 'Flower Room' AND cluster = 'back' 
   AND (mode IS NULL OR mode = 'DAY') 
   ORDER BY updated_at DESC LIMIT 1) AS value;

-- 5. Alternative: Try without mode filter
SELECT 
  generate_series(NOW() - INTERVAL '1 hour', NOW(), INTERVAL '10 minute') AS "time",
  'Temp Setpoint - Back' AS metric,
  (SELECT temperature FROM setpoints 
   WHERE location = 'Flower Room' AND cluster = 'back' 
   ORDER BY updated_at DESC LIMIT 1) AS value;

