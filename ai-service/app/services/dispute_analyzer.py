"""
Dispute Analysis Service for AI TrafficCam
AI-powered analysis of violation disputes
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import numpy as np
import cv2
from pathlib import Path
import json

from app.core.logging import logger
from app.core.config import settings


class DisputeCategory(str, Enum):
    """Categories of dispute claims."""
    WRONG_VEHICLE = "wrong_vehicle"
    NO_VIOLATION = "no_violation"
    EMERGENCY = "emergency"
    TECHNICAL_ERROR = "technical_error"
    WRONG_LOCATION = "wrong_location"
    VEHICLE_NOT_OWNED = "vehicle_not_owned"
    OTHER = "other"


class DisputeRecommendation(str, Enum):
    """Recommended actions for disputes."""
    ACCEPT = "accept"  # Accept user's dispute, dismiss violation
    REJECT = "reject"  # Reject dispute, uphold violation
    REVIEW = "review"  # Needs human review
    PARTIAL = "partial"  # Partially valid, reduce fine


@dataclass
class DisputeEvidence:
    """Evidence provided in dispute."""
    type: str  # image, document, video, text
    path: Optional[str] = None
    content: Optional[str] = None
    analysis_result: Optional[Dict[str, Any]] = None


@dataclass
class DisputeAnalysisResult:
    """Result of dispute analysis."""
    dispute_id: str
    category: DisputeCategory
    recommendation: DisputeRecommendation
    confidence: float
    reasoning: str
    evidence_analysis: List[Dict[str, Any]]
    factors: Dict[str, float]  # Weighted factors in decision
    suggested_action: str
    human_review_required: bool
    analyzed_at: datetime = field(default_factory=datetime.now)


class DisputeAnalyzer:
    """
    AI-powered dispute analysis service.
    Analyzes disputes and provides recommendations.
    """
    
    # Keywords indicating specific dispute types
    CATEGORY_KEYWORDS = {
        DisputeCategory.WRONG_VEHICLE: [
            'wrong vehicle', 'not my car', 'different vehicle', 
            'sold', 'transferred', 'not mine', 'wrong number'
        ],
        DisputeCategory.NO_VIOLATION: [
            'no violation', 'did not', 'didn\'t', 'was green',
            'not red', 'followed rules', 'legal', 'proper'
        ],
        DisputeCategory.EMERGENCY: [
            'emergency', 'hospital', 'accident', 'medical',
            'ambulance', 'fire', 'police escort', 'urgent'
        ],
        DisputeCategory.TECHNICAL_ERROR: [
            'error', 'mistake', 'glitch', 'wrong detection',
            'ai error', 'camera error', 'system error', 'bug'
        ],
        DisputeCategory.WRONG_LOCATION: [
            'wrong location', 'different place', 'wasn\'t there',
            'wrong city', 'wrong road', 'location error'
        ],
        DisputeCategory.VEHICLE_NOT_OWNED: [
            'stolen', 'theft', 'not owner', 'rental',
            'lent', 'borrowed', 'police report'
        ],
    }
    
    # Weight factors for decision
    DECISION_WEIGHTS = {
        'evidence_quality': 0.25,
        'claim_consistency': 0.20,
        'violation_confidence': 0.20,
        'category_validity': 0.15,
        'historical_record': 0.10,
        'documentation_complete': 0.10,
    }
    
    def __init__(self):
        self.analysis_cache = {}
    
    def analyze_dispute(
        self,
        dispute_id: str,
        user_statement: str,
        violation_data: Dict[str, Any],
        evidence_files: List[str] = None,
        user_history: Dict[str, Any] = None
    ) -> DisputeAnalysisResult:
        """
        Analyze a dispute and provide recommendation.
        
        Args:
            dispute_id: Unique dispute identifier
            user_statement: User's dispute statement
            violation_data: Original violation details
            evidence_files: Paths to evidence files
            user_history: User's past dispute/violation history
            
        Returns:
            DisputeAnalysisResult with recommendation
        """
        logger.info(f"Analyzing dispute: {dispute_id}")
        
        # Step 1: Categorize the dispute
        category = self._categorize_dispute(user_statement)
        
        # Step 2: Analyze provided evidence
        evidence_analysis = []
        if evidence_files:
            for file_path in evidence_files:
                analysis = self._analyze_evidence(file_path, category)
                evidence_analysis.append(analysis)
        
        # Step 3: Calculate decision factors
        factors = self._calculate_factors(
            category=category,
            statement=user_statement,
            violation_data=violation_data,
            evidence_analysis=evidence_analysis,
            user_history=user_history
        )
        
        # Step 4: Determine recommendation
        recommendation, confidence = self._determine_recommendation(factors)
        
        # Step 5: Generate reasoning
        reasoning = self._generate_reasoning(
            category=category,
            factors=factors,
            recommendation=recommendation,
            violation_data=violation_data
        )
        
        # Step 6: Determine if human review needed
        human_review = self._needs_human_review(confidence, category, factors)
        
        # Step 7: Generate suggested action
        suggested_action = self._generate_action(recommendation, category, factors)
        
        result = DisputeAnalysisResult(
            dispute_id=dispute_id,
            category=category,
            recommendation=recommendation,
            confidence=confidence,
            reasoning=reasoning,
            evidence_analysis=evidence_analysis,
            factors=factors,
            suggested_action=suggested_action,
            human_review_required=human_review
        )
        
        # Cache result
        self.analysis_cache[dispute_id] = result
        
        logger.info(f"Dispute {dispute_id} analyzed: {recommendation.value} ({confidence:.2f})")
        
        return result
    
    def _categorize_dispute(self, statement: str) -> DisputeCategory:
        """Categorize dispute based on statement."""
        statement_lower = statement.lower()
        
        category_scores = {}
        
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in statement_lower:
                    score += 1
            category_scores[category] = score
        
        # Get highest scoring category
        if category_scores:
            best_category = max(category_scores, key=category_scores.get)
            if category_scores[best_category] > 0:
                return best_category
        
        return DisputeCategory.OTHER
    
    def _analyze_evidence(
        self, 
        file_path: str,
        category: DisputeCategory
    ) -> Dict[str, Any]:
        """Analyze evidence file."""
        path = Path(file_path)
        
        analysis = {
            'file_path': file_path,
            'exists': path.exists(),
            'type': 'unknown',
            'quality_score': 0.0,
            'relevance_score': 0.0,
            'findings': []
        }
        
        if not path.exists():
            analysis['findings'].append("File not found")
            return analysis
        
        # Determine file type
        suffix = path.suffix.lower()
        
        if suffix in ['.jpg', '.jpeg', '.png', '.bmp']:
            analysis['type'] = 'image'
            analysis = self._analyze_image_evidence(path, analysis, category)
        elif suffix in ['.pdf']:
            analysis['type'] = 'document'
            analysis = self._analyze_document_evidence(path, analysis, category)
        elif suffix in ['.mp4', '.avi', '.mov']:
            analysis['type'] = 'video'
            analysis = self._analyze_video_evidence(path, analysis, category)
        else:
            analysis['type'] = 'other'
            analysis['findings'].append(f"Unsupported file type: {suffix}")
        
        return analysis
    
    def _analyze_image_evidence(
        self,
        path: Path,
        analysis: Dict[str, Any],
        category: DisputeCategory
    ) -> Dict[str, Any]:
        """Analyze image evidence."""
        try:
            image = cv2.imread(str(path))
            
            if image is None:
                analysis['findings'].append("Could not read image")
                return analysis
            
            h, w = image.shape[:2]
            analysis['dimensions'] = (w, h)
            
            # Quality assessment
            # Check blur
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            if laplacian_var < 100:
                analysis['findings'].append("Image appears blurry")
                analysis['quality_score'] = 0.3
            elif laplacian_var < 500:
                analysis['quality_score'] = 0.6
            else:
                analysis['quality_score'] = 0.9
            
            # Category-specific analysis
            if category == DisputeCategory.WRONG_VEHICLE:
                # Look for license plate
                plates = self._detect_plate_in_image(image)
                if plates:
                    analysis['findings'].append(f"Found {len(plates)} potential license plate(s)")
                    analysis['relevance_score'] = 0.8
                else:
                    analysis['findings'].append("No license plate detected")
                    analysis['relevance_score'] = 0.4
            
            elif category == DisputeCategory.EMERGENCY:
                # Look for hospital/emergency indicators
                # Check for medical colors (white, red cross, etc.)
                hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
                
                # Red cross pattern
                red_mask = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))
                red_pixels = cv2.countNonZero(red_mask)
                
                if red_pixels > (w * h * 0.01):
                    analysis['findings'].append("Medical/emergency indicators detected")
                    analysis['relevance_score'] = 0.7
                else:
                    analysis['relevance_score'] = 0.5
            
            elif category == DisputeCategory.VEHICLE_NOT_OWNED:
                # Look for document-like content (police report, etc.)
                # Text detection
                analysis['findings'].append("Document image - manual verification needed")
                analysis['relevance_score'] = 0.6
            
            else:
                analysis['relevance_score'] = 0.5
            
        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            analysis['findings'].append(f"Analysis error: {str(e)}")
        
        return analysis
    
    def _analyze_document_evidence(
        self,
        path: Path,
        analysis: Dict[str, Any],
        category: DisputeCategory
    ) -> Dict[str, Any]:
        """Analyze document evidence (PDFs, etc.)."""
        analysis['findings'].append("PDF document detected")
        analysis['quality_score'] = 0.7  # Assume reasonable quality
        
        # Category-specific relevance
        if category == DisputeCategory.VEHICLE_NOT_OWNED:
            analysis['findings'].append("May contain ownership/police report documents")
            analysis['relevance_score'] = 0.8
        elif category == DisputeCategory.EMERGENCY:
            analysis['findings'].append("May contain medical records")
            analysis['relevance_score'] = 0.8
        else:
            analysis['relevance_score'] = 0.5
        
        analysis['findings'].append("Manual document review recommended")
        
        return analysis
    
    def _analyze_video_evidence(
        self,
        path: Path,
        analysis: Dict[str, Any],
        category: DisputeCategory
    ) -> Dict[str, Any]:
        """Analyze video evidence."""
        try:
            cap = cv2.VideoCapture(str(path))
            
            if not cap.isOpened():
                analysis['findings'].append("Could not open video")
                return analysis
            
            # Get video properties
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            
            analysis['duration_seconds'] = duration
            analysis['frame_count'] = frame_count
            analysis['fps'] = fps
            
            # Sample frames for analysis
            sample_frames = min(5, frame_count)
            quality_scores = []
            
            for i in range(sample_frames):
                frame_pos = int(frame_count * i / sample_frames)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
                ret, frame = cap.read()
                
                if ret:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
                    quality_scores.append(min(blur_score / 500, 1.0))
            
            cap.release()
            
            if quality_scores:
                analysis['quality_score'] = sum(quality_scores) / len(quality_scores)
            
            if duration < 5:
                analysis['findings'].append("Video is very short")
                analysis['relevance_score'] = 0.4
            elif duration > 60:
                analysis['findings'].append("Video is long - may require extensive review")
                analysis['relevance_score'] = 0.6
            else:
                analysis['relevance_score'] = 0.7
            
            analysis['findings'].append(f"Video duration: {duration:.1f} seconds")
            
        except Exception as e:
            logger.error(f"Error analyzing video: {e}")
            analysis['findings'].append(f"Analysis error: {str(e)}")
        
        return analysis
    
    def _detect_plate_in_image(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect license plates in image."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply bilateral filter
        filtered = cv2.bilateralFilter(gray, 11, 17, 17)
        
        # Edge detection
        edges = cv2.Canny(filtered, 30, 200)
        
        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        plates = []
        for contour in contours:
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.018 * peri, True)
            
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h if h > 0 else 0
                
                if 2.0 < aspect_ratio < 5.0:
                    plates.append((x, y, x + w, y + h))
        
        return plates
    
    def _calculate_factors(
        self,
        category: DisputeCategory,
        statement: str,
        violation_data: Dict[str, Any],
        evidence_analysis: List[Dict[str, Any]],
        user_history: Optional[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Calculate weighted decision factors."""
        factors = {}
        
        # Evidence quality
        if evidence_analysis:
            avg_quality = sum(e.get('quality_score', 0) for e in evidence_analysis) / len(evidence_analysis)
            avg_relevance = sum(e.get('relevance_score', 0) for e in evidence_analysis) / len(evidence_analysis)
            factors['evidence_quality'] = (avg_quality + avg_relevance) / 2
        else:
            factors['evidence_quality'] = 0.2  # Low score if no evidence
        
        # Claim consistency (based on statement length and coherence)
        words = statement.split()
        if len(words) < 10:
            factors['claim_consistency'] = 0.3  # Too short
        elif len(words) > 500:
            factors['claim_consistency'] = 0.6  # Verbose
        else:
            factors['claim_consistency'] = 0.7
        
        # Violation confidence from original detection
        violation_confidence = violation_data.get('confidence', 0.8)
        factors['violation_confidence'] = 1 - violation_confidence  # Inverse - higher violation confidence = lower dispute favor
        
        # Category validity
        category_validity_map = {
            DisputeCategory.EMERGENCY: 0.7,
            DisputeCategory.VEHICLE_NOT_OWNED: 0.7,
            DisputeCategory.WRONG_VEHICLE: 0.6,
            DisputeCategory.WRONG_LOCATION: 0.5,
            DisputeCategory.TECHNICAL_ERROR: 0.4,
            DisputeCategory.NO_VIOLATION: 0.3,
            DisputeCategory.OTHER: 0.3,
        }
        factors['category_validity'] = category_validity_map.get(category, 0.3)
        
        # Historical record
        if user_history:
            total_disputes = user_history.get('total_disputes', 0)
            accepted_disputes = user_history.get('accepted_disputes', 0)
            
            if total_disputes > 0:
                acceptance_rate = accepted_disputes / total_disputes
                factors['historical_record'] = acceptance_rate
            else:
                factors['historical_record'] = 0.5  # Neutral for first-time
        else:
            factors['historical_record'] = 0.5
        
        # Documentation completeness
        required_docs = {
            DisputeCategory.VEHICLE_NOT_OWNED: 2,  # Police report + ownership proof
            DisputeCategory.EMERGENCY: 1,  # Medical certificate
            DisputeCategory.WRONG_VEHICLE: 1,  # Vehicle photo
        }
        
        required = required_docs.get(category, 0)
        provided = len(evidence_analysis) if evidence_analysis else 0
        
        if required > 0:
            factors['documentation_complete'] = min(provided / required, 1.0)
        else:
            factors['documentation_complete'] = 0.5 if provided > 0 else 0.3
        
        return factors
    
    def _determine_recommendation(
        self, 
        factors: Dict[str, float]
    ) -> Tuple[DisputeRecommendation, float]:
        """Determine recommendation based on weighted factors."""
        # Calculate weighted score
        weighted_sum = 0
        total_weight = 0
        
        for factor_name, weight in self.DECISION_WEIGHTS.items():
            if factor_name in factors:
                weighted_sum += factors[factor_name] * weight
                total_weight += weight
        
        score = weighted_sum / total_weight if total_weight > 0 else 0.5
        
        # Determine recommendation based on score
        if score >= 0.7:
            recommendation = DisputeRecommendation.ACCEPT
            confidence = score
        elif score >= 0.5:
            recommendation = DisputeRecommendation.REVIEW
            confidence = 0.5 + (score - 0.5)
        elif score >= 0.35:
            recommendation = DisputeRecommendation.PARTIAL
            confidence = 0.4 + score
        else:
            recommendation = DisputeRecommendation.REJECT
            confidence = 1 - score
        
        return recommendation, min(confidence, 0.95)
    
    def _generate_reasoning(
        self,
        category: DisputeCategory,
        factors: Dict[str, float],
        recommendation: DisputeRecommendation,
        violation_data: Dict[str, Any]
    ) -> str:
        """Generate human-readable reasoning for the decision."""
        parts = []
        
        parts.append(f"Dispute categorized as: {category.value.replace('_', ' ').title()}")
        
        # Evidence assessment
        evidence_score = factors.get('evidence_quality', 0)
        if evidence_score >= 0.7:
            parts.append("Evidence provided is of good quality and relevant.")
        elif evidence_score >= 0.4:
            parts.append("Evidence provided is of moderate quality.")
        else:
            parts.append("Evidence is insufficient or of poor quality.")
        
        # Violation confidence
        violation_conf = factors.get('violation_confidence', 0)
        original_conf = 1 - violation_conf
        parts.append(f"Original violation was detected with {original_conf*100:.0f}% confidence.")
        
        # Category-specific reasoning
        if category == DisputeCategory.EMERGENCY:
            parts.append("Emergency situations are given special consideration under traffic rules.")
        elif category == DisputeCategory.VEHICLE_NOT_OWNED:
            parts.append("Claims of vehicle theft/sale require police report verification.")
        elif category == DisputeCategory.WRONG_VEHICLE:
            parts.append("License plate verification needed to confirm vehicle identity.")
        
        # Recommendation explanation
        if recommendation == DisputeRecommendation.ACCEPT:
            parts.append("Based on the analysis, the dispute appears valid and is recommended for acceptance.")
        elif recommendation == DisputeRecommendation.REJECT:
            parts.append("Based on the analysis, the evidence does not support the dispute claim.")
        elif recommendation == DisputeRecommendation.REVIEW:
            parts.append("The case requires human review due to conflicting factors.")
        else:
            parts.append("A partial reduction may be appropriate based on circumstances.")
        
        return " ".join(parts)
    
    def _needs_human_review(
        self,
        confidence: float,
        category: DisputeCategory,
        factors: Dict[str, float]
    ) -> bool:
        """Determine if human review is required."""
        # Low confidence always needs review
        if confidence < 0.6:
            return True
        
        # Certain categories always need review
        if category in [DisputeCategory.EMERGENCY, DisputeCategory.VEHICLE_NOT_OWNED]:
            return True
        
        # Conflicting evidence
        evidence_score = factors.get('evidence_quality', 0)
        violation_conf = factors.get('violation_confidence', 0)
        
        if abs(evidence_score - (1 - violation_conf)) > 0.4:
            return True
        
        return False
    
    def _generate_action(
        self,
        recommendation: DisputeRecommendation,
        category: DisputeCategory,
        factors: Dict[str, float]
    ) -> str:
        """Generate suggested action for the dispute."""
        if recommendation == DisputeRecommendation.ACCEPT:
            return "Dismiss the violation and update violation status to 'dismissed'."
        elif recommendation == DisputeRecommendation.REJECT:
            return "Uphold the violation. Notify user of rejection with reasoning."
        elif recommendation == DisputeRecommendation.PARTIAL:
            reduction = int((1 - factors.get('violation_confidence', 0.5)) * 50)
            return f"Consider reducing fine by {reduction}% based on circumstances."
        else:
            if category == DisputeCategory.EMERGENCY:
                return "Verify medical/emergency documents and review with supervisor."
            elif category == DisputeCategory.VEHICLE_NOT_OWNED:
                return "Cross-verify with police records and vehicle registration database."
            else:
                return "Escalate to human reviewer for manual assessment."


# Singleton instance
dispute_analyzer = DisputeAnalyzer()
