CREATE TABLE dws_channel_registration_day AS
SELECT registration_date AS event_date, channel_id, COUNT(DISTINCT user_id) AS registrations FROM dwd_user_registration GROUP BY registration_date, channel_id;
