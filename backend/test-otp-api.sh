#!/bin/bash
# OTP Verification System - API Test Script
# Complete test flow for signup → verify → resend
# 
# Usage: chmod +x test-otp-api.sh && ./test-otp-api.sh

BASE_URL="http://localhost:5001/api/v1"
TEST_EMAIL="testuser$(date +%s)@example.com"
TEST_PASSWORD="Test@123"

echo "==========================================="
echo "🧪 OTP API Testing Script"
echo "==========================================="
echo ""
echo "Test Email: $TEST_EMAIL"
echo "Base URL: $BASE_URL"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Signup (Create account + send OTP)
echo "-------------------------------------------"
echo "Test 1: Signup (POST /auth/signup)"
echo "-------------------------------------------"

SIGNUP_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/signup" \
  -H "Content-Type: application/json" \
  -d "{
    \"firstName\": \"Test\",
    \"lastName\": \"User\",
    \"email\": \"$TEST_EMAIL\",
    \"password\": \"$TEST_PASSWORD\",
    \"gender\": \"male\",
    \"phone\": \"+919876543210\"
  }")

echo "Response:"
echo "$SIGNUP_RESPONSE" | jq '.'

if echo "$SIGNUP_RESPONSE" | jq -e '.success' > /dev/null; then
  echo -e "${GREEN}✅ Signup successful${NC}"
  USER_ID=$(echo "$SIGNUP_RESPONSE" | jq -r '.data.userId')
  echo "User ID: $USER_ID"
else
  echo -e "${RED}❌ Signup failed${NC}"
  exit 1
fi

echo ""
echo -e "${YELLOW}📧 Check your email ($TEST_EMAIL) for the OTP${NC}"
echo "Enter the OTP you received:"
read OTP

echo ""

# Test 2: Verify OTP
echo "-------------------------------------------"
echo "Test 2: Verify OTP (POST /auth/verify-otp)"
echo "-------------------------------------------"

VERIFY_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/verify-otp" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$TEST_EMAIL\",
    \"otp\": \"$OTP\"
  }")

echo "Response:"
echo "$VERIFY_RESPONSE" | jq '.'

if echo "$VERIFY_RESPONSE" | jq -e '.success' > /dev/null; then
  echo -e "${GREEN}✅ OTP verification successful${NC}"
  ACCESS_TOKEN=$(echo "$VERIFY_RESPONSE" | jq -r '.data.accessToken')
  echo "Access Token: ${ACCESS_TOKEN:0:50}..."
else
  echo -e "${RED}❌ OTP verification failed${NC}"
  
  # Test 3: Resend OTP (if verification failed)
  echo ""
  echo "-------------------------------------------"
  echo "Test 3: Resend OTP (POST /auth/resend-otp)"
  echo "-------------------------------------------"
  
  RESEND_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/resend-otp" \
    -H "Content-Type: application/json" \
    -d "{
      \"email\": \"$TEST_EMAIL\"
    }")
  
  echo "Response:"
  echo "$RESEND_RESPONSE" | jq '.'
  
  if echo "$RESEND_RESPONSE" | jq -e '.success' > /dev/null; then
    echo -e "${GREEN}✅ OTP resent successfully${NC}"
    echo ""
    echo -e "${YELLOW}📧 Check your email for the new OTP${NC}"
    echo "Enter the new OTP:"
    read NEW_OTP
    
    # Retry verification with new OTP
    echo ""
    echo "Verifying new OTP..."
    VERIFY_RETRY=$(curl -s -X POST "$BASE_URL/auth/verify-otp" \
      -H "Content-Type: application/json" \
      -d "{
        \"email\": \"$TEST_EMAIL\",
        \"otp\": \"$NEW_OTP\"
      }")
    
    echo "Response:"
    echo "$VERIFY_RETRY" | jq '.'
    
    if echo "$VERIFY_RETRY" | jq -e '.success' > /dev/null; then
      echo -e "${GREEN}✅ Verification successful with new OTP${NC}"
      ACCESS_TOKEN=$(echo "$VERIFY_RETRY" | jq -r '.data.accessToken')
    else
      echo -e "${RED}❌ Verification failed again${NC}"
      exit 1
    fi
  else
    echo -e "${RED}❌ Resend OTP failed${NC}"
    exit 1
  fi
fi

echo ""

# Test 4: Get OTP Status
echo "-------------------------------------------"
echo "Test 4: OTP Status (POST /auth/otp-status)"
echo "-------------------------------------------"

STATUS_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/otp-status" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$TEST_EMAIL\"
  }")

echo "Response:"
echo "$STATUS_RESPONSE" | jq '.'

echo ""

# Test 5: Get User Profile (with token)
echo "-------------------------------------------"
echo "Test 5: Get Profile (GET /auth/me)"
echo "-------------------------------------------"

PROFILE_RESPONSE=$(curl -s -X GET "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN")

echo "Response:"
echo "$PROFILE_RESPONSE" | jq '.'

if echo "$PROFILE_RESPONSE" | jq -e '.success' > /dev/null; then
  echo -e "${GREEN}✅ Profile retrieved successfully${NC}"
  IS_VERIFIED=$(echo "$PROFILE_RESPONSE" | jq -r '.data.user.isEmailVerified')
  echo "Email Verified: $IS_VERIFIED"
else
  echo -e "${RED}❌ Profile retrieval failed${NC}"
fi

echo ""
echo "==========================================="
echo -e "${GREEN}✅ ALL TESTS COMPLETED!${NC}"
echo "==========================================="
echo ""
echo "Summary:"
echo "- Email: $TEST_EMAIL"
echo "- User ID: $USER_ID"
echo "- Email Verified: $IS_VERIFIED"
echo "- Access Token: ${ACCESS_TOKEN:0:30}..."
echo ""
