from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index
from sqlalchemy.sql import func
from database import Base

class UserMapping(Base):
    __tablename__ = "user_mappings"

    id = Column(Integer, primary_key=True, index=True)
    
    # WhatsApp broj (ključ pretrage)
    phone_number = Column(String(50), unique=True, nullable=False)
    
    # Interni API User (Email ili UUID)
    api_identity = Column(String(100), nullable=False)
    
    # Ime za personalizaciju ("Bok Marko")
    display_name = Column(String(100), nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Indeks za instant dohvat aktivnih korisnika
    __table_args__ = (
        Index('idx_phone_active', 'phone_number', 'is_active'),
    )