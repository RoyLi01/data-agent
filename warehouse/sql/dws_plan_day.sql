CREATE TABLE dws_plan_day AS
WITH r AS (SELECT registration_date AS event_date, plan_id, COUNT(DISTINCT user_id) AS registrations FROM acquisitions GROUP BY 1,2)
SELECT s.event_date,s.plan_id,s.amount,COALESCE(r.registrations,0) AS registrations FROM plan_spend s LEFT JOIN r ON s.event_date=r.event_date AND s.plan_id=r.plan_id;
