-- 지갑 주소는 "0x" + 16진수 40자 = 42자로 고정 길이다.
-- 한 지갑이 두 계정에 연결되지 않도록 DB 차원에서 막는다.
-- 애플리케이션에서도 중복을 검사하지만, 동시에 들어온 요청은 코드 검사를 함께 통과할 수 있다.

UPDATE users
SET wallet_address = LOWER(wallet_address)
WHERE wallet_address IS NOT NULL;

ALTER TABLE users
    MODIFY COLUMN wallet_address VARCHAR(42) NULL;

ALTER TABLE users
    ADD CONSTRAINT uk_users_wallet_address UNIQUE (wallet_address);
