-- Switch to testdb database
CREATE DATABASE IF NOT EXISTS testdb;
USE testdb;

-- Create a test table
CREATE TABLE IF NOT EXISTS test_table (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Add some sample data
INSERT INTO test_table (name) VALUES 
    ('Sample Item 1'),
    ('Sample Item 2'),
    ('Sample Item 3');

-- Create a second table for testing relationships
CREATE TABLE IF NOT EXISTS test_table_metadata (
    id INT AUTO_INCREMENT PRIMARY KEY,
    test_table_id INT NOT NULL,
    meta_key VARCHAR(50) NOT NULL,
    meta_value TEXT,
    FOREIGN KEY (test_table_id) REFERENCES test_table(id) ON DELETE CASCADE
);

-- Add some sample metadata
INSERT INTO test_table_metadata (test_table_id, meta_key, meta_value) VALUES
    (1, 'description', 'This is sample item 1'),
    (1, 'category', 'Category A'),
    (2, 'description', 'This is sample item 2'),
    (2, 'category', 'Category B'),
    (3, 'description', 'This is sample item 3'),
    (3, 'category', 'Category A');

-- Create a simple user for testing purposes
-- (In a real environment, you'd use more secure credentials)
CREATE USER IF NOT EXISTS 'testuser'@'%' IDENTIFIED BY 'testpass';
GRANT ALL PRIVILEGES ON testdb.* TO 'testuser'@'%';
FLUSH PRIVILEGES;

-- Print success message
SELECT 'MySQL initialization completed successfully' AS 'Init Status';