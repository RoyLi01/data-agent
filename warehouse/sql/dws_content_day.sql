CREATE TABLE dws_content_day AS
WITH e AS (SELECT event_date,content_id,COUNT(*) AS exposures FROM exposures GROUP BY 1,2),
p AS (SELECT e.event_date,e.content_id,COUNT(DISTINCT p.exposure_id) AS converted_exposures,COUNT(p.play_id) AS plays FROM plays p JOIN exposures e ON p.exposure_id=e.exposure_id GROUP BY 1,2),
i AS (SELECT e.event_date,e.content_id,COUNT(i.interaction_id) AS interactions FROM interactions i JOIN plays p ON i.play_id=p.play_id JOIN exposures e ON p.exposure_id=e.exposure_id GROUP BY 1,2)
SELECT e.event_date,e.content_id,e.exposures,COALESCE(p.converted_exposures,0) AS converted_exposures,COALESCE(p.plays,0) AS plays,COALESCE(i.interactions,0) AS interactions FROM e LEFT JOIN p ON e.event_date=p.event_date AND e.content_id=p.content_id LEFT JOIN i ON e.event_date=i.event_date AND e.content_id=i.content_id;
