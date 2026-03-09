"""SQLAlchemy ORM models for FCC data."""

from datetime import date

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RadioService(Base):
    """FCC Radio Service code reference table."""

    __tablename__ = "radio_services"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    licenses: Mapped[list["License"]] = relationship(back_populates="service")

    def __repr__(self) -> str:
        return f"<RadioService {self.code}: {self.description}>"


class License(Base):
    """FCC license record."""

    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    unique_system_identifier: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    callsign: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    licensee_name: Mapped[str | None] = mapped_column(Text)
    radio_service: Mapped[str | None] = mapped_column(
        String(4), ForeignKey("radio_services.code"), index=True
    )
    status: Mapped[str | None] = mapped_column(String(5))
    grant_date: Mapped[date | None] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    frn: Mapped[str | None] = mapped_column(String(10))

    # Relationships
    service: Mapped[RadioService | None] = relationship(back_populates="licenses")
    locations: Mapped[list["Location"]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_licenses_callsign_status", "callsign", "status"),
    )

    def __repr__(self) -> str:
        return f"<License {self.callsign}>"


class Location(Base):
    """Transmitter / receiver location for a license."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    license_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("licenses.id", ondelete="CASCADE"), index=True
    )
    location_number: Mapped[int | None] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    county: Mapped[str | None] = mapped_column(String(100), index=True)
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    radius_km: Mapped[float | None] = mapped_column(Float)
    ground_elevation: Mapped[float | None] = mapped_column(Float)
    lat_degrees: Mapped[int | None] = mapped_column(Integer)
    lat_minutes: Mapped[int | None] = mapped_column(Integer)
    lat_seconds: Mapped[float | None] = mapped_column(Float)
    lat_direction: Mapped[str | None] = mapped_column(String(1))
    long_degrees: Mapped[int | None] = mapped_column(Integer)
    long_minutes: Mapped[int | None] = mapped_column(Integer)
    long_seconds: Mapped[float | None] = mapped_column(Float)
    long_direction: Mapped[str | None] = mapped_column(String(1))

    # PostGIS geometry column (SRID 4326 = WGS84)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=True)

    # Relationships
    license: Mapped[License] = relationship(back_populates="locations")
    frequencies: Mapped[list["Frequency"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_locations_state_county", "state", "county"),
        Index("ix_locations_geom", "geom", postgresql_using="gist"),
        Index("ix_locations_lat_lng", "latitude", "longitude"),
    )

    def __repr__(self) -> str:
        return f"<Location {self.state} {self.county} ({self.latitude}, {self.longitude})>"


class Frequency(Base):
    """Frequency assignment for a location."""

    __tablename__ = "frequencies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    frequency_mhz: Mapped[float | None] = mapped_column(Float, index=True)
    frequency_upper_mhz: Mapped[float | None] = mapped_column(Float)
    emission_designator: Mapped[str | None] = mapped_column(String(30))
    power: Mapped[float | None] = mapped_column(Float)
    station_class: Mapped[str | None] = mapped_column(String(20))
    unit_of_power: Mapped[str | None] = mapped_column(String(10))

    # Parsed emission designator components
    emission_bandwidth: Mapped[str | None] = mapped_column(String(20))
    emission_modulation: Mapped[str | None] = mapped_column(String(100))
    emission_signal_type: Mapped[str | None] = mapped_column(String(100))

    # Relationships
    location: Mapped[Location] = relationship(back_populates="frequencies")

    __table_args__ = (
        Index("ix_frequencies_freq_range", "frequency_mhz", "frequency_upper_mhz"),
    )

    def __repr__(self) -> str:
        return f"<Frequency {self.frequency_mhz} MHz>"


class CountyBoundary(Base):
    """US County boundary polygons for local reverse geocoding."""

    __tablename__ = "county_boundaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state_fips: Mapped[str] = mapped_column(String(2), nullable=False)
    county_fips: Mapped[str] = mapped_column(String(3), nullable=False)
    state_abbrev: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    county_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # PostGIS geometry column for polygon (SRID 4326 = WGS84)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)

    __table_args__ = (
        Index("ix_county_boundaries_geom", "geom", postgresql_using="gist"),
        Index("ix_county_boundaries_state_county", "state_abbrev", "county_name"),
    )

    def __repr__(self) -> str:
        return f"<CountyBoundary {self.county_name}, {self.state_abbrev}>"
