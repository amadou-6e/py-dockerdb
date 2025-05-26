-- This is a sample initialization script for Cassandra

-- Create a test keyspace if it doesn't exist already
CREATE KEYSPACE IF NOT EXISTS testkeyspace 
WITH REPLICATION = { 'class' : 'SimpleStrategy', 'replication_factor' : 1 };

-- Use the test keyspace
USE testkeyspace;

-- Create a test table
CREATE TABLE IF NOT EXISTS test_table (
    id uuid PRIMARY KEY,
    name text,
    value int
);

-- Insert some test data
INSERT INTO test_table (id, name, value) VALUES (uuid(), 'test1', 100);
INSERT INTO test_table (id, name, value) VALUES (uuid(), 'test2', 200);