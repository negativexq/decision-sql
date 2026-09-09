INSERT INTO subscribers VALUES (1,'Aster Mobile','North'),(2,'Boreal Voice','South'),(3,'Cinder Fiber','East'),(4,'Dune Wireless','West');
INSERT INTO plans VALUES (1,'Standard',50),(2,'Pro',90),(3,'Enterprise',250);
INSERT INTO subscriptions VALUES (101,1,1,'active'),(102,1,2,'cancelled'),(103,2,2,'active'),(104,3,3,'paused'),(105,4,1,'active');
INSERT INTO usage_records VALUES (201,1,'2026-06-01',100,'{"overhead_kb":"600"}'),(202,1,'2026-06-02',200,'{"overhead_kb":"10"}'),(203,2,'2026-06-03',500,'{"overhead_kb":"800"}'),(204,3,'2026-05-01',50,'{"overhead_kb":"100"}');
INSERT INTO invoices VALUES (301,1,'2026-06-01',50,'open'),(302,2,'2026-06-01',90,'paid'),(303,3,'2026-06-01',250,'open');
INSERT INTO payments VALUES (401,302,'2026-06-05',90,'posted');
INSERT INTO outages VALUES (501,'North','2026-06-02 10:00+00','2026-06-02 12:00+00'),(502,'South','2026-05-02 10:00+00','2026-05-02 11:00+00');
INSERT INTO external_directory VALUES (601,1,'owner@example.invalid');
