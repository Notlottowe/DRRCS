-- One-time update for the existing Cloudflare D1 data (UC-1 to UC-11).
-- prepare_cloud.py uses INSERT OR IGNORE, so rows that already exist do not get the new fields.
-- Run once after deploying the new backend, then run the regenerated seed.sql to add the new rows
-- (WATER-002, MED-001, GEN-001, RPT-DEMA2345, RPT-DEMB6789). Existing rows keep their data.
UPDATE records SET data=json_set(data,'$.name','Responder','$.qualifications',json('["Firefighting","EMT","Water Rescue"]'),'$.location',json('[41.881,-87.629]')),version=version+1
 WHERE kind='responders' AND id='responder' AND json_extract(data,'$.qualifications') IS NULL;
UPDATE records SET data=json_set(data,'$.name','Public Works Team 2','$.qualifications',json('["Search and Rescue","Structural Engineering"]'),'$.location',json('[41.9,-87.66]')),version=version+1
 WHERE kind='responders' AND id='team-2' AND json_extract(data,'$.qualifications') IS NULL;
UPDATE records SET data=json_set(data,'$.category','Rescue boats','$.status','Available'),version=version+1
 WHERE kind='resources' AND id='VEH-001' AND json_extract(data,'$.category') IS NULL;
UPDATE records SET data=json_set(data,'$.category','Water','$.status','Available'),version=version+1
 WHERE kind='resources' AND id='WATER-001' AND json_extract(data,'$.category') IS NULL;
