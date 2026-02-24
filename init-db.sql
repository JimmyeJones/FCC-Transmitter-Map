-- Initialize PostGIS extension and create tables
CREATE EXTENSION IF NOT EXISTS postgis;

-- Radio Services reference table
CREATE TABLE IF NOT EXISTS radio_services (
    code VARCHAR(4) PRIMARY KEY,
    description TEXT NOT NULL DEFAULT ''
);

-- Licenses
CREATE TABLE IF NOT EXISTS licenses (
    id BIGSERIAL PRIMARY KEY,
    unique_system_identifier BIGINT UNIQUE,
    callsign VARCHAR(20) NOT NULL,
    licensee_name TEXT,
    radio_service VARCHAR(4) REFERENCES radio_services(code),
    status VARCHAR(5),
    grant_date DATE,
    expiration_date DATE,
    effective_date DATE,
    frn VARCHAR(10)
);

-- Locations
CREATE TABLE IF NOT EXISTS locations (
    id BIGSERIAL PRIMARY KEY,
    license_id BIGINT NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
    location_number INTEGER,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    county VARCHAR(100),
    state VARCHAR(2),
    radius_km DOUBLE PRECISION,
    ground_elevation DOUBLE PRECISION,
    lat_degrees INTEGER,
    lat_minutes INTEGER,
    lat_seconds DOUBLE PRECISION,
    lat_direction VARCHAR(1),
    long_degrees INTEGER,
    long_minutes INTEGER,
    long_seconds DOUBLE PRECISION,
    long_direction VARCHAR(1),
    geom GEOMETRY(POINT, 4326)
);

-- Frequencies
CREATE TABLE IF NOT EXISTS frequencies (
    id BIGSERIAL PRIMARY KEY,
    location_id BIGINT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    frequency_mhz DOUBLE PRECISION,
    frequency_upper_mhz DOUBLE PRECISION,
    emission_designator VARCHAR(30),
    power DOUBLE PRECISION,
    station_class VARCHAR(20),
    unit_of_power VARCHAR(10),
    emission_bandwidth VARCHAR(20),
    emission_modulation VARCHAR(100),
    emission_signal_type VARCHAR(100)
);

-- Indexes
CREATE INDEX IF NOT EXISTS ix_licenses_callsign ON licenses (callsign);
CREATE INDEX IF NOT EXISTS ix_licenses_radio_service ON licenses (radio_service);
CREATE INDEX IF NOT EXISTS ix_licenses_callsign_status ON licenses (callsign, status);
CREATE INDEX IF NOT EXISTS ix_licenses_usi ON licenses (unique_system_identifier);

CREATE INDEX IF NOT EXISTS ix_locations_license_id ON locations (license_id);
CREATE INDEX IF NOT EXISTS ix_locations_state ON locations (state);
CREATE INDEX IF NOT EXISTS ix_locations_county ON locations (county);
CREATE INDEX IF NOT EXISTS ix_locations_state_county ON locations (state, county);
CREATE INDEX IF NOT EXISTS ix_locations_geom ON locations USING GIST (geom);

CREATE INDEX IF NOT EXISTS ix_frequencies_location_id ON frequencies (location_id);
CREATE INDEX IF NOT EXISTS ix_frequencies_frequency_mhz ON frequencies (frequency_mhz);
CREATE INDEX IF NOT EXISTS ix_frequencies_freq_range ON frequencies (frequency_mhz, frequency_upper_mhz);
