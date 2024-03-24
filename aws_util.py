import boto3


class AWSUtil:
    # 아래 값들은 환경변수로 관리
    AWS_ACCESS_KEY_ID = ''
    AWS_SECRET_ACCESS_KEY = ''
    EC2_PRIVATE_KEY = ''

    def __init__(self):
        self.vpc_id = ''
        self.default_port = 8000
        self.default_region = 'ap-northeast-2'
        self.ec2_client = boto3.client(
            'ec2',
            self.default_region,
            aws_access_key_id=self.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=self.AWS_SECRET_ACCESS_KEY
        )
        self.ec2_resource = boto3.resource(
            'ec2',
            self.default_region,
            aws_access_key_id=self.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=self.AWS_SECRET_ACCESS_KEY
        )
        self.elb_client = boto3.client(
            'elbv2',
            self.default_region,
            aws_access_key_id=self.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=self.AWS_SECRET_ACCESS_KEY
        )

    @staticmethod
    def make_instance_name(identifier):
        """
        ec2 인스턴스 이름 생성
        - 각자 rule에 맞게 이름 생성정
        """

        instance_name = identifier

        return instance_name

    @staticmethod
    def make_target_group_name(identifier: str, port: str):
        """
        target group 이름 생성
        - 각자 rule에 맞게 이름 생성
        """

        target_group_name = 'TG-' + identifier + '-' + port

        return target_group_name

    def add_instance(self, image_id, security_group, key, instance_type, vol_size, tags):
        response = self.ec2_client.run_instances(
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',
                    'Ebs': {
                        'DeleteOnTermination': True,
                        # 'SnapshotId': 'string',
                        'VolumeSize': vol_size,
                        'VolumeType': 'gp2',
                    },
                },
            ],
            SecurityGroupIds=[
                security_group,
            ],
            ImageId=image_id,
            InstanceType=instance_type,
            KeyName=key,
            MaxCount=1,
            MinCount=1,
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [tags],
                },
            ],

        )
        instance_id = response['Instances'][0]['InstanceId']

        return instance_id

    def add_target_group(self, identifier, instance_id, port):
        """
        target group 생성 및 target group에 인스턴스 등록
        """

        target_group_arn_list = []

        target_group_name = self.make_target_group_name(identifier=identifier, port=port)

        # 타겟그룹 생성
        response = self.elb_client.create_target_group(
            Name=target_group_name,
            Protocol='HTTP',
            Port=port,
            VpcId=self.vpc_id,
            HealthCheckProtocol='HTTP',
            HealthCheckEnabled=True,
            HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=5,
            HealthyThresholdCount=5,
            UnhealthyThresholdCount=2,
            Matcher={'HttpCode': '200,404'},
            TargetType='instance',
        )
        for target_group in response['TargetGroups']:
            target_group_arn_list.append(target_group['TargetGroupArn'])

        # 타겟그룹에 인스턴스 등록
        for target_group_arn in target_group_arn_list:
            self.elb_client.register_targets(
                TargetGroupArn=target_group_arn,
                Targets=[
                    {
                        'Id': instance_id,
                        'Port': port,
                    },
                ]
            )

        return target_group_arn_list

    def set_rule_priorities_in_order(self, listener_arn, new_rule_arn):
        """
        elb 리스너의 rules의 priority를 순서대로 재설정
        새로 생성한 rule의 priority를 1로 설정
        """

        response = self.elb_client.describe_rules(
            ListenerArn=listener_arn
        )

        rules = response['Rules']

        # 새로 생성한 rule를 제외한 rules 배열
        rules_except_new_rule = []
        for rule in rules:
            if rule['RuleArn'] != new_rule_arn and rule['Priority'] != 'default':
                rules_except_new_rule.append(rule)

        # 새로 생성한 Rule 제외한 rules의 priority 1씩 미뤄서 새로 설정
        rule_priorities_list = []
        for index, rule in enumerate(rules_except_new_rule):
            # priority 값은 1부터 시작
            # 새로 생성한 Rule 제외한 rules의 priority를 2부터 설정
            rule_priority_dict = {
                'RuleArn': rule['RuleArn'],
                'Priority': index + 2
            }
            rule_priorities_list.append(rule_priority_dict)

        # 새로 생성한 Rule의 priority를 1로 설정
        new_rule_priority_dict = {
            'RuleArn': new_rule_arn,
            'Priority': 1
        }
        rule_priorities_list.append(new_rule_priority_dict)

        self.elb_client.set_rule_priorities(
            RulePriorities=rule_priorities_list
        )

    def add_elb_rule(self, elb_listener, target_group_arn_list):
        """
        elb listener rule 생성
        - 인스턴스 대상그룹을 사용하는 경우에 한정
        - host-header, path-pattern을 사용하는 경우에 한정
        """

        target_group_config_list = []
        for target_group_arn in target_group_arn_list:
            target_group_config = {
                'TargetGroupArn': target_group_arn,
                'Weight': 1
            }
            target_group_config_list.append(target_group_config)

        response = self.elb_client.create_rule(
            ListenerArn=elb_listener,
            Conditions=[
                # 해당되는 조건 추가
                # {
                #     'Field': 'host-header',
                #     'Values': [],
                # },
                # {
                #     'Field': 'path-pattern',
                #     'Values': [],
                # },
            ],
            Priority=100,
            Actions=[
                {
                    'Type': 'forward',
                    'ForwardConfig': {
                        'TargetGroups': target_group_config_list,
                        'TargetGroupStickinessConfig': {
                            'Enabled': False,
                        }
                    }
                },
            ],
        )

        # rule 생성 최신순으로 priority 재설정
        self.set_rule_priorities_in_order(listener_arn=elb_listener, new_rule_arn=response['Rules'][0]['RuleArn'])

        return response

    def add_to_load_balancer(self, identifier, elb_listener, instance_id, target_num=1):
        """
        target groups(대상그룹) 및 listener rule(규칙) 생성
        - 전달받은 대상그룹의 개수에 따라 대상그룹 생성
        - default_port를 기준으로 1씩 증가한 값을 각자 port 값으로 갖는 다수의 대상그룹 생성.
        """

        target_group_arn_list = []

        # 대상그룹(Target Group) 생성
        for i in range(target_num):
            port = self.default_port + i
            target_group_arn = self.add_target_group(identifier=identifier, instance_id=instance_id, port=port)
            target_group_arn_list += target_group_arn

        # ELB 리스너 rule 추가
        self.add_elb_rule(elb_listener=elb_listener, target_group_arn_list=target_group_arn_list)

    def setup_instance(self, identifier, image_id, security_group, instance_type, vol_size, elb_listener, target_num=1):
        """
        ec2 인스턴스 생성 및 로드밸런서에 추가
        1. ec2 인스턴스 생성
        2. 로드밸런서에 추가
            - 대상그룹 생성
            - 로드밸런서 rule에 추가
            - rule priority 재설정
        """

        instance_name = self.make_instance_name(identifier=identifier)
        tags = {'Key': 'Name', 'Value': instance_name}

        # 전달받은 인자들의 정보로 서버(EC2)를 생성
        added_instance_id = self.add_instance(
            image_id=image_id,
            security_group=security_group,
            key=self.EC2_PRIVATE_KEY,
            instance_type=instance_type,
            vol_size=vol_size,
            tags=tags
        )

        # 인스턴스가 작동할 때까지 wait
        instance = self.ec2_resource.Instance(added_instance_id)
        instance.wait_until_running()

        # 로드밸런서 추가
        self.add_to_load_balancer(identifier=identifier, elb_listener=elb_listener, instance_id=added_instance_id, target_num=target_num)

        result = {'id': added_instance_id, 'is_created': True}

        return result

