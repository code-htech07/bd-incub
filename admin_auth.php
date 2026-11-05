<?php

function respondAdminAuth(int $status, string $message): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    echo json_encode(array('status' => 'error', 'message' => $message), JSON_UNESCAPED_UNICODE);
    exit;
}

function requireAdmin(): void
{
    $expected = getenv('UPB_ADMIN_TOKEN');
    if (!is_string($expected) || strlen($expected) < 10) {
        respondAdminAuth(503, 'Accès administrateur non configuré. Définissez UPB_ADMIN_TOKEN (10 caractères minimum).');
    }

    $authorization = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    if ($authorization === '' && function_exists('getallheaders')) {
        $headers = getallheaders();
        foreach ($headers as $name => $value) {
            if (strtolower($name) === 'authorization') {
                $authorization = $value;
                break;
            }
        }
    }

    if (!preg_match('/^Bearer\s+(.+)$/i', trim($authorization), $matches)
        || !hash_equals($expected, $matches[1])) {
        respondAdminAuth(401, 'Accès réservé aux administrateurs.');
    }
}
