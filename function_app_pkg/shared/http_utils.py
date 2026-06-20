"""HTTP utilities"""
import json
import azure.functions as func

def json_response(status: int, data=None, error=None):
    body = {}
    if error:
        body = {'success': False, 'error': error}
    elif data:
        body = {'success': True, 'data': data}
    else:
        body = {'success': True}
    
    return func.HttpResponse(
        body=json.dumps(body, default=str, indent=2),
        status_code=status,
        mimetype='application/json',
        headers={'Access-Control-Allow-Origin': '*'}
    )