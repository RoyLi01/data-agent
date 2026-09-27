CREATE TABLE dws_channel_activity_day AS
SELECT event_date, channel_id, COUNT(DISTINCT user_id) AS active_users FROM dwd_user_activity GROUP BY event_date,channel_id;
