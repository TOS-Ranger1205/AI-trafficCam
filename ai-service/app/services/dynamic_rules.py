"""
Dynamic Rule Fetcher for AI Service

This module fetches violation rules from the backend database to ensure
the AI service always uses the latest admin-configured rules.
"""

import httpx
import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from app.core.config import settings
from app.core.logging import logger


class ViolationSeverity(str, Enum):
    """Severity levels for violations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class DatabaseViolationRule:
    """Violation rule from backend database."""
    id: str
    violation_type: str
    violation_code: str
    name: str
    description: str
    base_fine_amount: float
    repeat_offender_multiplier: float
    max_fine_amount: Optional[float]
    grace_period_days: int
    late_fee_penalty: float
    points_deduction: int
    is_active: bool
    ai_detection_enabled: bool
    min_confidence_threshold: float
    
    def to_ai_rule(self) -> Dict[str, Any]:
        """Convert to format used by AI rule engine."""
        return {
            "name": self.name,
            "violation_type": self.violation_type,
            "description": self.description,
            "fine_amount": int(self.base_fine_amount),
            "min_confidence": self.min_confidence_threshold / 100.0,  # Convert % to decimal
            "is_active": self.is_active,
            "ai_enabled": self.ai_detection_enabled,
            "params": {
                "violation_code": self.violation_code,
                "max_fine": int(self.max_fine_amount) if self.max_fine_amount else None,
                "repeat_multiplier": float(self.repeat_offender_multiplier),
                "points": int(self.points_deduction),
            }
        }


class RuleFetcher:
    """Fetches violation rules from backend database."""
    
    def __init__(self):
        self.backend_url = getattr(settings, 'backend_url', 'http://localhost:5001')
        self.cache: Dict[str, DatabaseViolationRule] = {}
        self.last_fetch: float = 0
        self.cache_duration: int = 60  # 60 seconds cache
        self.client: Optional[httpx.AsyncClient] = None
    
    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=self.backend_url,
                timeout=httpx.Timeout(10.0),
                headers={"Content-Type": "application/json"}
            )
        return self.client
    
    async def close(self):
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    async def fetch_rules_from_api(self) -> List[DatabaseViolationRule]:
        """Fetch fresh rules from backend API."""
        try:
            client = await self.get_client()
            
            # Try to get an admin token or use system auth
            response = await client.get("/api/v1/admin/violation-rules")
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch rules: HTTP {response.status_code}")
                return []
            
            data = response.json()
            if not data.get("success"):
                logger.warning(f"API returned error: {data.get('message', 'Unknown error')}")
                return []
            
            rules_data = data.get("data", {}).get("rules", [])
            rules = []
            
            for rule_data in rules_data:
                try:
                    rule = DatabaseViolationRule(
                        id=rule_data["id"],
                        violation_type=rule_data["violationType"],
                        violation_code=rule_data["violationCode"],
                        name=rule_data["name"],
                        description=rule_data.get("description", ""),
                        base_fine_amount=float(rule_data["baseFineAmount"]),
                        repeat_offender_multiplier=float(rule_data["repeatOffenderMultiplier"]),
                        max_fine_amount=float(rule_data["maxFineAmount"]) if rule_data.get("maxFineAmount") else None,
                        grace_period_days=int(rule_data["gracePeriodDays"]),
                        late_fee_penalty=float(rule_data["lateFeePenalty"]),
                        points_deduction=int(rule_data["pointsDeduction"]),
                        is_active=bool(rule_data["isActive"]),
                        ai_detection_enabled=bool(rule_data["aiDetectionEnabled"]),
                        min_confidence_threshold=float(rule_data["minConfidenceThreshold"])
                    )
                    rules.append(rule)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse rule {rule_data.get('id', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Successfully fetched {len(rules)} violation rules from database")
            return rules
            
        except httpx.RequestError as e:
            logger.warning(f"Network error fetching rules: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching rules: {e}")
            return []
    
    async def get_active_rules(self, force_refresh: bool = False) -> Dict[str, DatabaseViolationRule]:
        """
        Get active violation rules with caching.
        
        Args:
            force_refresh: Force fetch from database, ignore cache
            
        Returns:
            Dict mapping violation_type to DatabaseViolationRule
        """
        current_time = time.time()
        
        # Check if we need to refresh cache
        if force_refresh or (current_time - self.last_fetch) > self.cache_duration:
            logger.debug("Fetching fresh violation rules from database...")
            
            rules = await self.fetch_rules_from_api()
            
            # Update cache with active, AI-enabled rules only
            self.cache = {
                rule.violation_type: rule
                for rule in rules
                if rule.is_active and rule.ai_detection_enabled
            }
            
            self.last_fetch = current_time
            logger.info(f"Updated rule cache with {len(self.cache)} active rules")
        
        return self.cache
    
    async def get_rule(self, violation_type: str) -> Optional[DatabaseViolationRule]:
        """Get a specific rule by violation type."""
        rules = await self.get_active_rules()
        return rules.get(violation_type)
    
    async def get_confidence_threshold(self, violation_type: str) -> float:
        """Get AI confidence threshold for a specific violation type."""
        rule = await self.get_rule(violation_type)
        if rule:
            return rule.min_confidence_threshold / 100.0  # Convert % to decimal
        
        # Fallback defaults
        return 0.75  # 75%
    
    async def get_ai_rules_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all rules in format expected by AI rule engine.
        
        Returns:
            Dict mapping violation_type to rule configuration
        """
        rules = await self.get_active_rules()
        
        config = {}
        for violation_type, rule in rules.items():
            config[violation_type] = rule.to_ai_rule()
        
        logger.debug(f"Generated AI config for {len(config)} violation types")
        return config


# Global rule fetcher instance
rule_fetcher = RuleFetcher()


async def get_rule_fetcher() -> RuleFetcher:
    """Get global rule fetcher instance."""
    return rule_fetcher


async def shutdown_rule_fetcher():
    """Cleanup rule fetcher."""
    await rule_fetcher.close()


# Convenience functions for backward compatibility
async def get_active_rules() -> Dict[str, DatabaseViolationRule]:
    """Get all active rules."""
    return await rule_fetcher.get_active_rules()


async def get_confidence_threshold(violation_type: str) -> float:
    """Get confidence threshold for violation type."""
    return await rule_fetcher.get_confidence_threshold(violation_type)


async def refresh_rules() -> Dict[str, DatabaseViolationRule]:
    """Force refresh rules from database."""
    return await rule_fetcher.get_active_rules(force_refresh=True)