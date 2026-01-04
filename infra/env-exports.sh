#!/bin/bash
# Environment variables for all Tic-Tac-Toe services
# Source this file to set all environment variables:
#   source env-exports.sh
# Or run it to export them in the current shell:
#   . env-exports.sh

# =============================================================================
# KAFKA CONFIGURATION (Shared by all services)
# =============================================================================
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export KAFKA_STATE_TOPIC=game-state-updates
export KAFKA_MOVES_TOPIC=game-moves

# =============================================================================
# GAME LOGIC SERVICE
# =============================================================================
export KAFKA_CONSUMER_GROUP=game-logic-service
# KAFKA_BOOTSTRAP_SERVERS - see above
# KAFKA_MOVES_TOPIC - see above
# KAFKA_STATE_TOPIC - see above

# =============================================================================
# GATEWAY SERVICE
# =============================================================================
export GATEWAY_HOST=0.0.0.0
export GATEWAY_PORT=9000
# KAFKA_BOOTSTRAP_SERVERS - see above
# KAFKA_STATE_TOPIC - see above

# =============================================================================
# ARCHIVE SERVICE
# =============================================================================
export KAFKA_CONSUMER_GROUP=archive-service
# KAFKA_BOOTSTRAP_SERVERS - see above
# KAFKA_STATE_TOPIC - see above

# MongoDB Configuration
export MONGO_URL=mongodb://localhost:27017
export MONGO_DB_NAME=tic-tac-toe
export MONGO_USE_AUTH=false

# MongoDB Authentication (only needed if MONGO_USE_AUTH=true)
# export MONGO_USERNAME=your_username
# export MONGO_PASSWORD=your_password

# =============================================================================
# STATISTICS SERVICE
# =============================================================================
# MONGO_URL - see above (shared with Archive Service)
# MONGO_DB_NAME - see above (shared with Archive Service)
# MONGO_USE_AUTH - see above (shared with Archive Service)
# MONGO_USERNAME - see above (shared with Archive Service)
# MONGO_PASSWORD - see above (shared with Archive Service)

export STATISTICS_HOST=0.0.0.0
export STATISTICS_PORT=8000

# =============================================================================
# NOTES
# =============================================================================
# - All services prioritize OS environment variables over config file defaults
# - Uncomment and set MONGO_USERNAME/MONGO_PASSWORD if using MongoDB authentication
# - Adjust KAFKA_BOOTSTRAP_SERVERS if Kafka is running on a different host/port
# - Adjust MONGO_URL if MongoDB is running on a different host/port

