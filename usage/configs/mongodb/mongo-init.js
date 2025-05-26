// MongoDB Initialization Script
// This script runs when the database container starts

// Switch to the target database
db = db.getSiblingDB('demodb');

// Create a test collection with sample data
db.test_collection.insertOne({
    name: 'Test Entry',
    environment: process.env.YourEnvVar || 'DefaultValue',
    created_at: new Date(),
    type: 'initialization_test'
});

// Create indexes
db.test_collection.createIndex({ "name": 1 });
db.test_collection.createIndex({ "environment": 1 });

// Create a sample function (stored as a document for later use)
db.system_functions.insertOne({
    name: 'get_env_info',
    function: function() {
        var doc = db.test_collection.findOne({type: 'initialization_test'});
        return 'Environment: ' + (doc ? doc.environment : 'Not Set');
    }.toString(),
    created_at: new Date()
});

// Create initial collections with schema validation
db.createCollection("users", {
    validator: {
        $jsonSchema: {
            bsonType: "object",
            required: ["username", "email"],
            properties: {
                username: {
                    bsonType: "string",
                    description: "must be a string and is required"
                },
                email: {
                    bsonType: "string",
                    pattern: "^.+@.+$",
                    description: "must be a valid email and is required"
                },
                age: {
                    bsonType: "int",
                    minimum: 0,
                    maximum: 150,
                    description: "must be an integer between 0 and 150"
                }
            }
        }
    }
});

// Insert sample users
db.users.insertMany([
    {
        username: "init_user1",
        email: "init1@example.com",
        age: 25,
        created_at: new Date(),
        source: "initialization"
    },
    {
        username: "init_user2", 
        email: "init2@example.com",
        age: 30,
        created_at: new Date(),
        source: "initialization"
    }
]);

print("MongoDB initialization completed successfully!");