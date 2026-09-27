CREATE TABLE dwd_user_activity AS
SELECT a.user_id, a.event_date, u.channel_id, u.os FROM ods_activity a JOIN dwd_user_registration u ON a.user_id=u.user_id;
