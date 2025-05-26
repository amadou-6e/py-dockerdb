-- MSSQL Initialization Script
-- This script runs when the database container starts

-- Ensure we're using the correct database
USE demodb;

-- Create table if it doesn't exist
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='test_table' AND xtype='U')
CREATE TABLE test_table (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(255),
    environment NVARCHAR(100)
);

-- Insert test data using environment variable (passed via environment)
-- Note: MSSQL doesn't support environment variables directly in SQL like MySQL
-- So we'll handle this through the application layer
INSERT INTO test_table (name, environment) 
VALUES ('Test Entry', '$(YourEnvVar)');

GO;
-- Create a sample function
CREATE OR ALTER FUNCTION dbo.get_env_info()
RETURNS NVARCHAR(500)
AS
BEGIN
    DECLARE @env_value NVARCHAR(255);
    SELECT TOP 1 @env_value = environment FROM test_table;
    RETURN 'Environment: ' + ISNULL(@env_value, 'Not Set');
END;
GO;