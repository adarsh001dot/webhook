"""
===========================================
🔔 PAYMENT WEBHOOK HANDLER
===========================================
This file handles payment gateway callbacks
Developer: @VIP_X_OFFICIAL
Version: 1.0
===========================================
"""

from flask import Flask, request, jsonify
from pymongo import MongoClient
from datetime import datetime
from pytz import timezone  # <-- ADDED THIS
import logging
import requests
import json
import os
import hmac
import hashlib

# ==================== CONFIGURATION ====================
MONGODB_URI = "mongodb+srv://nikilsaxena843_db_user:3gF2wyT4IjsFt0cY@vipbot.puv6gfk.mongodb.net/?appName=vipbot"
BOT_TOKEN = "8294367270:AAFbCzMXn3vTAcYxCNeOMPOdwtcSN8GpQnE"
OWNER_ID = 7459756974
OWNER_USERNAME = "@VIP_X_OFFICIAL"

# India Timezone
IST = timezone('Asia/Kolkata')  # <-- NOW THIS WILL WORK

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# ==================== DATABASE CONNECTION ====================
try:
    client = MongoClient(MONGODB_URI)
    db = client['tg_to_num_test']
    
    # Collections
    users_col = db['users']
    orders_col = db['orders']
    transactions_col = db['transactions']
    payment_logs_col = db['payment_logs']
    
    print("✅ Webhook Database Connected Successfully!")
    print(f"📊 Connected to: tg_to_num_test")
    
except Exception as e:
    print(f"❌ Webhook Database Error: {e}")
    exit(1)

# ==================== HELPER FUNCTIONS ====================
def get_ist():
    """Get current IST time"""
    return datetime.now(IST)

def format_ist(dt):
    """Format IST datetime"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone('UTC')).astimezone(IST)
    return dt.strftime("%d-%m-%Y %I:%M:%S %p")

def send_telegram_message(chat_id, text, parse_mode='HTML'):
    """Send message to Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return None

# ==================== WEBHOOK ENDPOINT ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle payment gateway webhook"""
    try:
        # Get data from request
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
        
        logger.info(f"Webhook received: {json.dumps(data)}")
        
        # Log raw webhook data
        with open('webhook_log.txt', 'a') as f:
            f.write(f"\n--- {get_ist()} ---\n")
            f.write(json.dumps(data, indent=2))
            f.write("\n")
        
        # Extract payment information
        status = data.get('status')
        order_id = data.get('order_id')
        gateway_order_id = data.get('gateway_order_id') or data.get('orderId')
        remark1 = data.get('remark1')  # This contains user_id
        remark2 = data.get('remark2')  # This contains points info
        utr = data.get('utr') or data.get('transaction_id')
        amount = data.get('amount')
        
        # Check if this is a success notification
        is_success = False
        if status == 'SUCCESS' or data.get('resultInfo') == 'Transaction Success':
            is_success = True
        elif data.get('status') == True and data.get('message') == 'Order Created Successfully':
            # This is order creation response, not payment webhook
            return jsonify({"status": "received"}), 200
        
        if is_success and order_id:
            # Find order in database
            order = orders_col.find_one({'order_id': order_id})
            
            if not order and gateway_order_id:
                # Try finding by gateway order ID
                order = orders_col.find_one({'gateway_order_id': gateway_order_id})
            
            if not order:
                # Try to extract from remark1 (user_id) if available
                if remark1 and remark1.isdigit():
                    user_id = int(remark1)
                    
                    # Extract points from remark2
                    points = 0
                    if remark2 and remark2.startswith('points_'):
                        try:
                            points = int(remark2.replace('points_', ''))
                        except:
                            pass
                    
                    # Get amount
                    if not amount and points:
                        # Calculate amount based on points (adjust rate as needed)
                        amount = points * 5  # Assuming 1 point = ₹5
                    
                    if points > 0:
                        # Process payment without order record
                        process_successful_payment(
                            user_id=user_id,
                            amount=amount,
                            points=points,
                            order_id=order_id or f"MANUAL_{get_ist().strftime('%Y%m%d%H%M%S')}",
                            gateway_order_id=gateway_order_id,
                            utr=utr
                        )
                        
                        return jsonify({"status": "processed", "message": "Payment processed successfully"}), 200
                
                logger.error(f"Order not found: {order_id}")
                return jsonify({"status": "error", "message": "Order not found"}), 404
            
            # Check if already processed
            if order['status'] == 'completed':
                logger.info(f"Order {order_id} already completed")
                return jsonify({"status": "already_processed"}), 200
            
            # Process successful payment
            user_id = order['user_id']
            points = order['points']
            amount = order['amount']
            
            result = process_successful_payment(
                user_id=user_id,
                amount=amount,
                points=points,
                order_id=order_id,
                gateway_order_id=gateway_order_id or order.get('gateway_order_id'),
                utr=utr
            )
            
            if result:
                return jsonify({"status": "success", "message": "Payment processed"}), 200
            else:
                return jsonify({"status": "error", "message": "Failed to process"}), 500
        
        elif status == 'FAILED' or data.get('resultInfo') == 'Transaction Failed':
            # Handle failed payment
            if order_id:
                orders_col.update_one(
                    {'order_id': order_id},
                    {'$set': {
                        'status': 'failed',
                        'failed_at': get_ist()
                    }}
                )
                
                # Notify user about failure
                order = orders_col.find_one({'order_id': order_id})
                if order:
                    try:
                        send_telegram_message(
                            order['user_id'],
                            f"❌ Payment Failed!\n\n"
                            f"Order: {order_id}\n"
                            f"Amount: ₹{order['amount']}\n\n"
                            f"Please try again or contact admin {OWNER_USERNAME}"
                        )
                    except:
                        pass
                
                return jsonify({"status": "failed_recorded"}), 200
        
        return jsonify({"status": "received"}), 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def process_successful_payment(user_id, amount, points, order_id, gateway_order_id=None, utr=None):
    """Process successful payment - add points to user account"""
    try:
        # Check if already processed
        existing = transactions_col.find_one({
            'order_id': order_id,
            'type': 'credit',
            'reason': {'$regex': 'Payment'}
        })
        
        if existing:
            logger.info(f"Payment {order_id} already processed")
            return True
        
        # Add points to user
        user = users_col.find_one({'user_id': user_id})
        if not user:
            logger.error(f"User {user_id} not found")
            return False
        
        new_balance = user['points'] + points
        users_col.update_one(
            {'user_id': user_id},
            {'$set': {'points': new_balance}}
        )
        
        # Log transaction
        transactions_col.insert_one({
            'user_id': user_id,
            'type': 'credit',
            'amount': points,
            'reason': f"Payment for order {order_id}",
            'order_id': order_id,
            'gateway_order_id': gateway_order_id,
            'utr': utr,
            'balance': new_balance,
            'timestamp': get_ist()
        })
        
        # Update order status
        orders_col.update_one(
            {'order_id': order_id},
            {'$set': {
                'status': 'completed',
                'gateway_order_id': gateway_order_id,
                'utr': utr,
                'completed_at': get_ist()
            }},
            upsert=True
        )
        
        # Update payment log
        payment_logs_col.update_one(
            {'order_id': order_id},
            {'$set': {
                'status': 'success',
                'gateway_order_id': gateway_order_id,
                'utr': utr,
                'completed_at': get_ist()
            }},
            upsert=True
        )
        
        # Send success message to user
        user_name = user.get('first_name', 'User')
        success_message = (
            f"✅ <b>Payment Successful!</b>\n\n"
            f"💰 Amount: ₹{amount}\n"
            f"🎯 Points Added: {points}\n"
            f"💎 New Balance: {new_balance}\n\n"
            f"Thank you for your purchase!"
        )
        
        send_telegram_message(user_id, success_message)
        
        # Send notification to admin
        admin_message = (
            f"💳 <b>New Payment Received</b>\n\n"
            f"👤 User: {user_name}\n"
            f"🆔 ID: {user_id}\n"
            f"💰 Amount: ₹{amount}\n"
            f"🎯 Points: {points}\n"
            f"📦 Order: {order_id}\n"
            f"🔖 UTR: {utr or 'N/A'}\n"
            f"🕐 Time: {format_ist(get_ist())}"
        )
        
        send_telegram_message(OWNER_ID, admin_message)
        
        logger.info(f"Payment processed successfully for user {user_id}, order {order_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error processing payment: {e}")
        return False

# ==================== HEALTH CHECK ENDPOINT ====================
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": format_ist(get_ist()),
        "database": "connected"
    })

# ==================== TEST ENDPOINT ====================
@app.route('/test-webhook', methods=['POST'])
def test_webhook():
    """Test endpoint to simulate webhook"""
    data = request.get_json() or request.form.to_dict()
    
    # Log test
    with open('webhook_test_log.txt', 'a') as f:
        f.write(f"\n--- {get_ist()} ---\n")
        f.write(json.dumps(data, indent=2))
        f.write("\n")
    
    return jsonify({
        "status": "test_received",
        "data": data,
        "timestamp": format_ist(get_ist())
    })

# ==================== RUN APP ====================
if __name__ == '__main__':
    print("="*50)
    print("🔔 WEBHOOK SERVER STARTED")
    print("="*50)
    print(f"🕐 Time: {format_ist(get_ist())} IST")
    print(f"📊 Database: Connected")
    print(f"🌐 Webhook URL: http://your-domain.com/webhook")
    print(f"🔍 Test URL: http://your-domain.com/test-webhook")
    print(f"💓 Health URL: http://your-domain.com/health")
    print("="*50)
    print("✅ Ready to receive payment notifications!")
    print("="*50)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)
