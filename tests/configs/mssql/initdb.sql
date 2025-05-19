-- initdb.sql for SQL Server
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID('test_table'))
BEGIN
    CREATE TABLE test_table (
        id INT PRIMARY KEY IDENTITY(1,1),
        name NVARCHAR(100),
        created_at DATETIME DEFAULT GETDATE()
    );
    
    INSERT INTO test_table (name) VALUES ('Test Record 1');
    INSERT INTO test_table (name) VALUES ('Test Record 2');
END