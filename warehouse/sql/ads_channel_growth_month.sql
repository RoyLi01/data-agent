CREATE TABLE ads_channel_growth_month AS
WITH r AS (SELECT substr(registration_date,1,7) AS month, channel_id, COUNT(DISTINCT user_id) AS registrations FROM dwd_user_registration WHERE registration_date <= :complete_month_end AND registration_date >= :complete_month_start GROUP BY 1,2),
a AS (SELECT substr(event_date,1,7) AS month, channel_id, COUNT(DISTINCT user_id) AS active_users FROM dwd_user_activity WHERE event_date <= :complete_month_end AND event_date >= :complete_month_start GROUP BY 1,2)
SELECT COALESCE(r.month,a.month) AS month, COALESCE(r.channel_id,a.channel_id) AS channel_id, COALESCE(r.registrations,0) AS registrations, COALESCE(a.active_users,0) AS active_users FROM r FULL OUTER JOIN a ON r.month=a.month AND r.channel_id=a.channel_id;
