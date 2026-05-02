-- PostgreSQL initialization script
-- Runs once on first startup via Docker

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- for LIKE-based search indexes

-- Optimize for QR scan event queries
-- (actual index creation is handled by Django migrations)
