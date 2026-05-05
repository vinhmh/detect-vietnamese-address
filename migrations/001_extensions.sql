-- Enable fuzzy trigram search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable unaccent for optional use
CREATE EXTENSION IF NOT EXISTS unaccent;
