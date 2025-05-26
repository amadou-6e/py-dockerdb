-- MySQL Initialization Script
-- This script runs when the database container starts

-- Ensure we're using the correct database
USE demodb;

-- Create table if it doesn't exist
CREATE TABLE IF NOT EXISTS test_table (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    environment VARCHAR(100)
);

-- Insert test data using environment variable
INSERT INTO test_table (name, environment) 
VALUES ('Test Entry', @YourEnvVar);

-- Create a sample function
DELIMITER $$
CREATE FUNCTION IF NOT EXISTS get_env_info()
RETURNS VARCHAR(255)
READS SQL DATA
DETERMINISTIC
BEGIN
    DECLARE env_value VARCHAR(255);
    SELECT environment INTO env_value FROM test_table LIMIT 1;
    RETURN CONCAT('Environment: ', IFNULL(env_value, 'Not Set'));
END$$
DELIMITER ;