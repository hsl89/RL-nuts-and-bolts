from rlkits.policies import RandomPolicyWithValue
from rlkits.policies import PolicyWithValue
from rlkits.sampler import ParallelEnvTrajectorySampler
from rlkits.sampler import estimate_Q
from rlkits.env_batch import ParallelEnvBatch
from rlkits.sampler import ParallelEnvTrajectorySampler
from rlkits.env_wrappers import AutoReset, StartWithRandomActions

import gym
import pprint as pp
import numpy as np
import torch


def test_sampler():
    print('Test TrajectorySampler')
    env = gym.make('Pendulum-v0')
    nsteps = 3
    ob_space = env.observation_space
    ac_space = env.action_space

    agent = PolicyWithValue(ob_space=ob_space,
        ac_space=ac_space, hidden_layers=[4])
    
    sampler = TrajectorySampler(env, agent, nsteps)
    trajectory = sampler(callback=estimate_Q)
    pp.pprint(trajectory)
    return

def test_policy_with_value():
    print('Test PolicyWithValue')
    env = gym.make('Pendulum-v0')
    
    nsteps = 3
    ob_space = env.observation_space
    ac_space = env.action_space

    pi = PolicyWithValue(ob_space=ob_space,
        ac_space=ac_space, hidden_layers=[1024])
    
    sampler = TrajectorySampler(env, pi, nsteps, None)
    trajectory = sampler()
    pp.pprint(trajectory)
    
    print('Test batch inference')
    obs = trajectory['obs']
    obs = torch.from_numpy(obs).float()
    y = pi.policy_net(obs)
    print('input params to the distr', y)
    dist = pi.dist(y)
    
    sample_actions = np.zeros((10000, 3), np.float32)
    for i in range(10000):
        sample_actions[i] = dist.sample().numpy()

    print('mean actions', np.mean(sample_actions, axis=0))
    print('distr means', dist.mean)
    return

def test_parallel_env_sampler():    
    def make_env():
        env = gym.make('CartPole-v0').unwrapped
        env = AutoReset(env)
        env = StartWithRandomActions(env, max_random_actions=5)
        return env
    
    def reward_transform(rew):
        return (rew + 8.0) / 16.0

    env = ParallelEnvBatch(make_env, nenvs=4)

    #env = make_env()
    ob_space = env.observation_space
    ac_space = env.action_space
    
    pi = PolicyWithValue(
        ob_space=ob_space, ac_space=ac_space, ckpt_dir='/tmp', 
        hidden_layers=[1024])
    samp = ParallelEnvTrajectorySampler(env, pi, 32)
    for attr in ['obs', 'rews', 'vpreds', 'dones', 'actions', 'log_prob']:
        print(attr, getattr(samp, attr).shape)
    traj = samp(callback=estimate_Q)
    pp.pprint(traj)
    env.close()
    return


def generate_env():
    def make_env():
        env = gym.make('CartPole-v0').unwrapped
        env = AutoReset(env)
        env = StartWithRandomActions(env, max_random_actions=5)
        return env
    
    def reward_transform(rew):
        return (rew + 8.0) / 16.0

    env = ParallelEnvBatch(make_env, nenvs=4)
    return env

    

def test_policy_with_one_backbone():
    env = generate_env()
    ob_space = env.observation_space
    ac_space = env.action_space
    
    pi = PolicyWithValueSingleModel(
        ob_space = ob_space,
        ac_space = ac_space,
        ckpt_dir = '/tmp',
        hidden_layers = [1024]
    )
    
    
    samp = ParallelEnvTrajectorySampler(env, pi, 32)
    for attr in ['obs', 'rews', 'vpreds', 'dones', 'actions', 'log_prob']:
        print(attr, getattr(samp, attr).shape)
    traj = samp(callback=estimate_Q)
    pp.pprint(traj)
    env.close()
    return 


def profile():
    from rlkits.policies import PolicyWithValue
    from rlkits.env_batch import ParallelEnvBatch
    import numpy as np
    import gym
    from rlkits.sampler import ParallelEnvTrajectorySampler

    def make_env():
        return gym.make('Pendulum-v0')

    env = ParallelEnvBatch(make_env, nenvs=1024)

    #env = make_env()
    ob_space = env.observation_space
    ac_space = env.action_space

    pi = PolicyWithValue(
        ob_space=ob_space, ac_space=ac_space, ckpt_dir='/tmp', 
        hidden_layers=[1024])


    samp = ParallelEnvTrajectorySampler(env, pi, 12)
    for _ in range(100):
        traj = samp()
    env.close()
    return


def test_running_mean_std():
    from rlkits.running_mean_std import RunningMeanStd
    
    rms = RunningMeanStd(epsilon=1e-8, shape=(2, 5))
    
    total = np.zeros((100, 2, 5), dtype=np.float64)
    for i in range(100):
        x = np.random.rand(2, 5)
        total[i] = x
        rms.update(np.array([x]))
    
    
    np.testing.assert_almost_equal(rms.mean, np.mean(total, axis=0))
    np.testing.assert_almost_equal(rms.std, np.std(total, axis=0))
    return

if __name__=='__main__':
    #test_sampler()
    #test_policy_with_value()
    #test_parallel_env_sampler()
    #profile()
    #test_policy_with_one_backbone()
    test_running_mean_std()
