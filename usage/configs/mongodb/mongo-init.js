// Sample MongoDB initialization script
// This will be executed when the MongoDB container starts
// File should be placed at CONFIG_DIR/mongodb/initdb.js

// Create a test collection and insert a document
db = db.getSiblingDB('testdb');

// Drop the collection if it exists to ensure fresh state
db.test_collection.drop();

// Create test collection
db.createCollection('test_collection');

// Insert test document
db.test_collection.insertOne({
    test_field: 'test_value',
    created_at: new Date(),
    numeric_value: 42,
    nested_object: {
        key1: 'value1',
        key2: 'value2'
    },
    array_field: [1, 2, 3, 4, 5]
});

// Create an index
db.test_collection.createIndex({ test_field: 1 });

// Create a second collection
db.createCollection('another_collection');

// Add sample data
db.another_collection.insertMany([
    { name: 'Item 1', value: 10 },
    { name: 'Item 2', value: 20 },
    { name: 'Item 3', value: 30 }
]);

// Print completion message
print('MongoDB initialization completed successfully.');