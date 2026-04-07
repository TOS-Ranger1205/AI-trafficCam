#!/bin/bash

# OTP Frontend Test Script
# This script helps you test the OTP signup and verification flow

echo "=================================================="
echo "🚀 AI TrafficCam - OTP Frontend Test Script"
echo "=================================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if backend is running
echo -e "${BLUE}📡 Checking backend status...${NC}"
if curl -s http://localhost:5001/api/v1/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Backend is running on port 5001${NC}"
else
    echo -e "${RED}❌ Backend is NOT running!${NC}"
    echo -e "${YELLOW}Start backend: cd backend && npm run dev${NC}"
    exit 1
fi

# Check if frontend is running
echo -e "${BLUE}📱 Checking frontend status...${NC}"
if curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Frontend is running on port 5173${NC}"
else
    echo -e "${RED}❌ Frontend is NOT running!${NC}"
    echo -e "${YELLOW}Start frontend: cd frontend && npm run dev${NC}"
    exit 1
fi

echo ""
echo "=================================================="
echo "📋 Testing OTP Signup Flow"
echo "=================================================="
echo ""

# Generate random test user
RANDOM_NUM=$((1000 + RANDOM % 9000))
TEST_EMAIL="test.user.${RANDOM_NUM}@example.com"
TEST_FIRST_NAME="John"
TEST_LAST_NAME="Doe"
TEST_PASSWORD="SecurePass123!"
TEST_PHONE="+91 98765 43210"

echo -e "${BLUE}🧪 Test User Details:${NC}"
echo "First Name: $TEST_FIRST_NAME"
echo "Last Name: $TEST_LAST_NAME"
echo "Email: $TEST_EMAIL"
echo "Password: $TEST_PASSWORD"
echo "Phone: $TEST_PHONE"
echo "Gender: male"
echo ""

# Test 1: Signup
echo -e "${YELLOW}Test 1: POST /api/v1/auth/signup${NC}"
SIGNUP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://localhost:5001/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d "{
    \"firstName\": \"$TEST_FIRST_NAME\",
    \"lastName\": \"$TEST_LAST_NAME\",
    \"email\": \"$TEST_EMAIL\",
    \"password\": \"$TEST_PASSWORD\",
    \"gender\": \"male\",
    \"phone\": \"$TEST_PHONE\"
  }")

HTTP_CODE=$(echo "$SIGNUP_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$SIGNUP_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" -eq 201 ]; then
    echo -e "${GREEN}✅ Signup successful (201)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
    
    # Extract userId
    USER_ID=$(echo "$RESPONSE_BODY" | jq -r '.data.user.id')
    echo ""
    echo -e "${GREEN}📧 OTP email should be sent to: $TEST_EMAIL${NC}"
    echo -e "${YELLOW}⏰ OTP valid for: 5 minutes${NC}"
    echo ""
else
    echo -e "${RED}❌ Signup failed (HTTP $HTTP_CODE)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
    exit 1
fi

# Prompt for OTP
echo "=================================================="
echo -e "${BLUE}📬 Check your email for the OTP code${NC}"
echo "=================================================="
echo ""
read -p "Enter the 6-digit OTP: " OTP_CODE

if [ -z "$OTP_CODE" ] || [ ${#OTP_CODE} -ne 6 ]; then
    echo -e "${RED}❌ Invalid OTP format. Must be 6 digits.${NC}"
    exit 1
fi

# Test 2: Verify OTP
echo ""
echo -e "${YELLOW}Test 2: POST /api/v1/auth/verify-otp${NC}"
VERIFY_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://localhost:5001/api/v1/auth/verify-otp \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$TEST_EMAIL\",
    \"otp\": \"$OTP_CODE\"
  }")

HTTP_CODE=$(echo "$VERIFY_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$VERIFY_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" -eq 200 ]; then
    echo -e "${GREEN}✅ OTP verification successful (200)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
    echo ""
    echo -e "${GREEN}🎉 Account verified! User can now login.${NC}"
else
    echo -e "${RED}❌ OTP verification failed (HTTP $HTTP_CODE)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
    
    # Check if user wants to resend OTP
    echo ""
    read -p "Do you want to resend OTP? (y/n): " RESEND_CHOICE
    
    if [ "$RESEND_CHOICE" = "y" ] || [ "$RESEND_CHOICE" = "Y" ]; then
        echo ""
        echo -e "${YELLOW}Test 3: POST /api/v1/auth/resend-otp${NC}"
        RESEND_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://localhost:5001/api/v1/auth/resend-otp \
          -H "Content-Type: application/json" \
          -d "{\"email\": \"$TEST_EMAIL\"}")
        
        HTTP_CODE=$(echo "$RESEND_RESPONSE" | tail -n1)
        RESPONSE_BODY=$(echo "$RESEND_RESPONSE" | head -n-1)
        
        if [ "$HTTP_CODE" -eq 200 ]; then
            echo -e "${GREEN}✅ OTP resent successfully (200)${NC}"
            echo "Response: $RESPONSE_BODY" | jq '.'
            echo ""
            echo -e "${GREEN}📧 New OTP sent to: $TEST_EMAIL${NC}"
            echo ""
            read -p "Enter the new 6-digit OTP: " NEW_OTP_CODE
            
            # Verify new OTP
            VERIFY_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://localhost:5001/api/v1/auth/verify-otp \
              -H "Content-Type: application/json" \
              -d "{
                \"email\": \"$TEST_EMAIL\",
                \"otp\": \"$NEW_OTP_CODE\"
              }")
            
            HTTP_CODE=$(echo "$VERIFY_RESPONSE" | tail -n1)
            RESPONSE_BODY=$(echo "$VERIFY_RESPONSE" | head -n-1)
            
            if [ "$HTTP_CODE" -eq 200 ]; then
                echo -e "${GREEN}✅ OTP verification successful (200)${NC}"
                echo "Response: $RESPONSE_BODY" | jq '.'
                echo ""
                echo -e "${GREEN}🎉 Account verified! User can now login.${NC}"
            else
                echo -e "${RED}❌ OTP verification failed (HTTP $HTTP_CODE)${NC}"
                echo "Response: $RESPONSE_BODY" | jq '.'
            fi
        else
            echo -e "${RED}❌ Resend OTP failed (HTTP $HTTP_CODE)${NC}"
            echo "Response: $RESPONSE_BODY" | jq '.'
        fi
    fi
fi

# Test 4: Check OTP Status
echo ""
echo "=================================================="
echo -e "${YELLOW}Test 4: POST /api/v1/auth/otp-status${NC}"
STATUS_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST http://localhost:5001/api/v1/auth/otp-status \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$TEST_EMAIL\"}")

HTTP_CODE=$(echo "$STATUS_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$STATUS_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" -eq 200 ]; then
    echo -e "${GREEN}✅ OTP status retrieved (200)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
else
    echo -e "${RED}⚠️  OTP status check failed (HTTP $HTTP_CODE)${NC}"
    echo "Response: $RESPONSE_BODY" | jq '.'
fi

# Summary
echo ""
echo "=================================================="
echo "📊 Test Summary"
echo "=================================================="
echo ""
echo -e "${BLUE}Test User:${NC}"
echo "  Email: $TEST_EMAIL"
echo "  Password: $TEST_PASSWORD"
echo "  User ID: $USER_ID"
echo ""
echo -e "${BLUE}Frontend URLs:${NC}"
echo "  Signup: http://localhost:5173/signup"
echo "  Verify OTP: http://localhost:5173/verify-otp"
echo "  Login: http://localhost:5173/login"
echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo "  1. Open browser: http://localhost:5173/login"
echo "  2. Login with:"
echo "     Email: $TEST_EMAIL"
echo "     Password: $TEST_PASSWORD"
echo "  3. Verify you can access the dashboard"
echo ""
echo -e "${GREEN}✅ All tests completed!${NC}"
echo "=================================================="
